import os, random
import requests
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session, abort
from flask_login import login_required, current_user
from iknow_utils import get_by_id_or_404, role_required
from flask_mail import Mail
from models import User, Category, JobRequest, GalleryImage, Message, Review, fn, Transaction, db
from werkzeug.utils import secure_filename


customer_bp = Blueprint('customer', __name__)

UPLOAD_FOLDER = 'static/uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

@customer_bp.route('/dashboard')
@login_required
def dashboard():
    # Fetch all chats/jobs
    requests = JobRequest.select().where(
        JobRequest.customer == current_user
    ).order_by(JobRequest.created_at.desc())
    
    stats = {
        'messages': requests.where(JobRequest.status == 'pending').count(), # Conversations
        'hired': requests.where(JobRequest.status == 'active').count(),     # Paid projects
        'completed': requests.where(JobRequest.status == 'completed').count()
    }
    
    return render_template('customer/dashboard.html', requests=requests, stats=stats)

@customer_bp.route('/explore')
@login_required
def explore():
    if not current_user.full_verified:
        flash("Please complete your profile verification to access the marketplace.", "warning")
        return redirect(url_for('customer.settings'))
    # Filters
    cat_id = request.args.get('category')
    query = request.args.get('q')
    
    # --- POINT 1: THE SHADOW BAN ---
    # We only select users who are 'pro' AND 'full_verified'
    pros = User.select().where(
        (User.role == 'pro') & 
        (User.full_verified == True)
    )
    # -------------------------------
    
    if cat_id:
        pros = pros.where(User.category == cat_id)
    if query:
        # Combining filters using Peewee's bitwise operators
        pros = pros.where(
            (User.username.contains(query)) | 
            (User.location.contains(query))
        )
        
    categories = Category.select()
    return render_template('customer/explore.html', pros=pros, categories=categories)

@customer_bp.route('/my-requests')
@login_required
def my_requests():
    # Fetch all job requests made by the current customer, newest first
    requests = JobRequest.select().where(
        JobRequest.customer == current_user
    ).order_by(JobRequest.created_at.desc())
    
    return render_template('customer/my_requests.html', requests=requests)


@customer_bp.route('/hire/<string:pro_id>', methods=['GET'])
@login_required
@role_required('customer')
def hire_pro(pro_id):
    if not current_user.full_verified:
        flash("Please verify your email address to message professionals.", "warning")
        return redirect(url_for('customer.dashboard'))

    pro = User.get_or_none(User.public_id == pro_id)
    if not pro:
        abort(404)
    # 1. Look for an existing conversation
    job = JobRequest.select().where(
    (JobRequest.customer == current_user) & 
    (JobRequest.pro == pro) & 
    (JobRequest.status.in_(['chatting', 'quoted']))).first()
    print(job)
    # 2. If it exists, go to it.
    if job:
        return redirect(url_for('chat.view_chat', job_id=job.id))
    
    # 3. If NO job exists, go to a "New Chat" view without creating a DB row yet
    return redirect(url_for('chat.new_chat', pro_id=pro_id))

@customer_bp.route('/checkout/<int:job_id>')
@login_required
@role_required('customer')
def checkout(job_id):
    job = JobRequest.get_by_id(job_id)
    
    # 1. Security check: Only the customer who owns the request can pay
    if job.customer != current_user:
        abort(403)
        
    # 2. Prevent double payment
    if job.status == 'hired':
        flash("This project is already active and paid for.", "info")
        return redirect(url_for('chat.view_chat', job_id=job.id))

    # 3. Simulate Payment Integration 
    # (This is where you'll eventually add Paystack/Flutterwave logic)
    
    # 4. Update status to 'hired' to match our UI
    job.status = 'hired' 
    job.save()
    
    # 5. Success feedback
    flash(f"Payment successful! {job.pro.username} has been notified to start work.", "success")
    
    # 6. Redirect back to chat so they see the "Funds Secured" banner
    return redirect(url_for('chat.view_chat', job_id=job.id))

@customer_bp.route('/complete_job/<int:job_id>', methods=['POST'])
@login_required
def complete_job(job_id):
    job = JobRequest.get_by_id(job_id)
    
    if job.customer != current_user or job.status != 'hired':
        abort(403)

    with db.atomic() as txn:
        try:
            # 1. Update Job
            job.status = 'completed'
            job.save()

            # 2. Add funds to Pro
            pro = job.pro
            pro.balance += job.total_amount
            pro.save()

            # 3. Log transaction for Pro
            Transaction.create(user=pro, amount=job.total_amount, t_type='release',transaction_hash  = uuid.uuid4())

            flash("Project completed. Funds released to the pro!", "success")
        except Exception as e:
            txn.rollback()
            flash("Error releasing funds.", "danger")

    return redirect(url_for('chat.view_chat', job_id=job.id))


@customer_bp.route('/pay_from_wallet/<int:job_id>', methods=['POST'])
@login_required
def pay_from_wallet(job_id):
    job = JobRequest.get_by_id(job_id)
    
    if job.customer != current_user or job.status != 'quoted':
        abort(403)

    if current_user.balance < job.total_amount:
        flash("Insufficient wallet balance. Please top up.", "danger")
        return redirect(url_for('customer.wallet'))

    # Start Transaction
    with db.atomic() as txn:
        try:
            # 1. Deduct from Customer
            current_user.balance -= job.total_amount
            current_user.save()

            # 2. Update Job Status
            job.status = 'hired'
            job.save()

            # 3. Log the transaction
            Transaction.create(user=current_user, amount=-job.total_amount,transaction_hash = uuid.uuid4(), t_type='payment')
            
            flash("Payment successful! Funds held in escrow.", "success")
        except Exception as e:
            txn.rollback()
            flash("An error occurred during payment.", "danger")

    return redirect(url_for('chat.view_chat', job_id=job.id))


@customer_bp.route('/wallet')
@login_required
def wallet():
    # Fetch transactions, newest first
    transactions = Transaction.select().where(
        Transaction.user == current_user
    ).order_by(Transaction.timestamp.desc())
    
    return render_template('customer/wallet.html', transactions=transactions)


@customer_bp.route('/review_pro/<int:job_id>', methods=['POST'])
@login_required
def review_pro(job_id):
    job = JobRequest.get_by_id(job_id)
    
    # Validation
    if job.customer != current_user or job.status != 'completed':
        return "Unauthorized", 403

    rating_val = request.form.get('rating')
    comment_val = request.form.get('comment')

    # Explicitly creating and saving
    new_review = Review(
        job=job,
        customer=current_user,
        pro=job.pro,  # Pass the object
        rating=int(rating_val),
        comment=comment_val
    )
    
    success = new_review.save(force_insert=True) # force_insert ensures it hits the DB
    
    if success:
        flash("Review saved!", "success")
    else:
        flash("Failed to save review.", "danger")

    return redirect(url_for('chat.view_chat', job_id=job.id))


@customer_bp.route('/profile/<int:user_id>')
@login_required
def view_customer_profile(user_id):
    customer = User.get_by_id(user_id)
    
    # Safety check: if they try to view a Pro here, redirect to the Pro profile
    if customer.role == 'pro':
        return redirect(url_for('pro.view_profile', user_id=user_id))
        
    return render_template('customer/customer_profile_view.html', customer=customer)


@customer_bp.route('/pro/<string:pro_id>')
@login_required
@role_required('customer')
def view_pro_profile(pro_id):
    pro = User.get_or_none(User.public_id == pro_id)
    if not pro:
        abort(404)
    gallery = GalleryImage.select().where(GalleryImage.user == pro)
    
    # Get all reviews for this pro
    reviews = Review.select().where(Review.pro == pro).order_by(Review.created_at.desc())
    
    # Calculate Average Rating
    avg_rating = Review.select(fn.AVG(Review.rating)).where(Review.pro == pro).scalar() or 0
    review_count = reviews.count()
    
    return render_template('customer/view_pro.html', 
                           pro=pro, 
                           gallery=gallery, 
                           reviews=reviews, 
                           avg_rating=round(avg_rating, 1),
                           review_count=review_count)

@customer_bp.route('/support', methods=['GET', 'POST'])
@login_required
def support():
    if request.method == 'POST':
        category = request.form.get('category')
        
        # --- POINT 4: SUPPORT GUARD ---
        if not current_user.full_verified and category != 'verification':
            flash("Unverified accounts can only submit support tickets regarding account verification.", "warning")
            return redirect(url_for('customer.support'))
        # ------------------------------

        Ticket.create(
            customer=current_user,
            subject=request.form.get('subject'),
            message=request.form.get('message'),
            category=category
        )
        flash("Support ticket submitted! We'll get back to you via email.", "success")
        return redirect(url_for('customer.dashboard'))

    return render_template('customer/support.html')

@customer_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@role_required('customer')
def settings():
    user_otp_key = f"otp_data_{current_user.id}"
    phone_otp_key = f"phone_otp_{current_user.id}"
    # session.pop(phone_otp_key, None)
    if request.method == 'POST':
        action = request.form.get('action')

        # --- ACTION 1: UPDATE PROFILE ---
        if action == 'update_profile':
            current_user.username = request.form.get('username')
            current_user.email = request.form.get('email')
            current_user.phone = request.form.get('phone') 
            current_user.bio = request.form.get('bio')
            current_user.location = request.form.get('location')
            
            if 'profile_pic' in request.files:
                file = request.files['profile_pic']
                if file and file.filename != '':
                    ext = file.filename.rsplit('.', 1)[1].lower()
                    filename = secure_filename(f"user_{current_user.id}.{ext}")
                    file.save(os.path.join(UPLOAD_FOLDER, filename))
                    current_user.profile_pic = filename

            try:
                current_user.save(only=[User.username, User.location, User.bio, User.email, User.profile_pic, User.phone])
                flash("Account settings updated!", "success")
            except Exception as e:
                flash("Error updating account.", "danger")
            return redirect(url_for('customer.settings'))

        # --- ACTION 2: SEND EMAIL OTP ---
        elif action == 'send_otp':
            from flask_mail import Message as MailMessage
            mail = current_app.extensions.get('mail')
            otp = str(random.randint(100000, 999999))
            session[user_otp_key] = {
                'code': otp,
                'expiry': (datetime.now() + timedelta(minutes=10)).timestamp()
            }
            session.modified = True 

            try:
                msg = MailMessage("Verify your Iknowsome1 Account", 
                                  recipients=[current_user.email],
                                  sender=current_app.config.get('MAIL_USERNAME'))
                msg.html = render_template('pro/otp_email.html', otp=otp, user=current_user)
                mail.send(msg)
                flash("Verification code sent to your email!", "info")
            except Exception as e:
                session.pop(user_otp_key, None)
                flash("Failed to send email.", "danger")
            return redirect(url_for('customer.settings'))

        # --- ACTION 3: VERIFY EMAIL OTP ---
        elif action == 'verify_otp':
            user_otp = request.form.get('otp_input')
            otp_data = session.get(user_otp_key)
            if otp_data and user_otp == otp_data['code']:
                current_user.email_verified = True
                current_user.check_and_verify()
                if datetime.now().timestamp() < otp_data['expiry']:
                    current_user.email_verified = True
                    current_user.save(only=[User.email_verified])
                    session.pop(user_otp_key, None)
                    flash("Email verified successfully!", "success")
                else:
                    session.pop(user_otp_key, None)
                    flash("Code expired.", "danger")
            else:
                session.pop(user_otp_key, None)
                flash("Invalid code.", "danger")
            return redirect(url_for('customer.settings'))

        # --- ACTION 4: SEND ARKESEL SMS OTP (V2 FIXED) ---
        elif action == 'send_phone_otp':
            if not current_user.phone:
                flash("Please save your phone number first.", "warning")
                return redirect(url_for('customer.settings'))
            
            otp = str(random.randint(100000, 999999))
            session[phone_otp_key] = {
                'code': otp,
                'expiry': (datetime.now() + timedelta(minutes=5)).timestamp()
            }
            session.modified = True

            try:
                phone = current_user.phone
                if phone.startswith('0'):
                    phone = '233' + phone[1:]
                
                # Arkesel V2 Configuration
                headers = {
                    'api-key': 'YWN5c25XT0NvVGFOYVZWVXZ6aW0',
                    'Content-Type': 'application/json '# Put your real key here
                }
                
                payload = {
                    'sender': 'Iknowsome1', # Use 'Arkesel' until 'IKS1' is approved
                    'message': f"Your Iknowsome1 code is {otp}. Valid for 5 mins.",
                    'recipients': [phone],
                }
                
                # Arkesel V2 Endpoint
                response = requests.post(
                    "https://sms.arkesel.com/api/v2/sms/send", 
                    json=payload, 
                    headers=headers, 
                    timeout=10
                )
                
                res_data = response.json()
                # Arkesel V2 returns 201 for successful send
                if response.status_code in [200, 201] and res_data.get('status') == 'success':
                    flash("Verification code sent to your phone!", "info")
                else:
                    session.pop(phone_otp_key, None)
                    error_msg = res_data.get('message', 'Unknown Error')
                    flash(f"SMS Error: {error_msg}", "danger")

            except Exception as e:
                session.pop(phone_otp_key, None)
                flash(f"System Error: {str(e)}", "danger")
            return redirect(url_for('customer.settings'))

        # --- ACTION 5: VERIFY PHONE OTP ---
        elif action == 'verify_phone_otp':
            user_code = request.form.get('phone_otp_input')
            otp_data = session.get(phone_otp_key)
            if otp_data and user_code == otp_data['code']:
                current_user.phone_verified = True
                current_user.check_and_verify()
                if datetime.now().timestamp() < otp_data['expiry']:
                    current_user.phone_verified = True
                    current_user.save(only=[User.phone_verified])
                    session.pop(phone_otp_key, None)
                    flash("Phone number verified successfully!", "success")
                else:
                    session.pop(phone_otp_key, None)
                    flash("SMS code expired.", "danger")
            else:
                session.pop(phone_otp_key, None)
                flash("Invalid SMS code.", "danger")
            return redirect(url_for('customer.settings'))

        # --- ACTION 6: SUBMIT KYC ---
        elif action == 'submit_kyc':
            if 'kyc_doc' in request.files:
                file = request.files['kyc_doc']
                if file and file.filename != '':
                    ext = file.filename.rsplit('.', 1)[1].lower()
                    filename = secure_filename(f"kyc_cust_{current_user.id}_{int(datetime.now().timestamp())}.{ext}")
                    kyc_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'kyc')
                    if not os.path.exists(kyc_path):
                        os.makedirs(kyc_path)
                    file.save(os.path.join(kyc_path, filename))
                    current_user.kyc_status = 'pending'
                    current_user.kyc_document = filename 
                    current_user.save(only=[User.kyc_status, User.kyc_document])
                    flash("Identity document uploaded! We will review it shortly.", "success")
            return redirect(url_for('customer.settings'))

    return render_template('customer/settings.html')