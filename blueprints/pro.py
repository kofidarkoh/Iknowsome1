import os, random
from datetime import datetime, timedelta,date
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import Category, User, GalleryImage,JobRequest, fn, Message, Transaction, db
from iknow_utils import role_required, get_by_id_or_404, process_payment
from flask_mail import Message as MailMessage # Ensure flask-mail is installed
import mailtrap as mt


pro_bp = Blueprint('pro', __name__)

@pro_bp.route('/dashboard')
@login_required
@role_required('pro')
def dashboard():
    # 1. Get stats
    d = date.today()
    current_date = d.strftime("%A - %d %B (%m), %Y")
    
    # Total Earnings: Only count jobs where status is 'completed' (Funds Released)
    total_earnings = JobRequest.select(fn.SUM(JobRequest.total_amount)).where(
        (JobRequest.pro == current_user) & (JobRequest.status == 'completed')
    ).scalar() or 0
    
    # Active Projects: Count jobs where status is 'hired' (Funds Secured/Work in Progress)
    active_count = JobRequest.select().where(
        (JobRequest.pro == current_user) & (JobRequest.status == 'hired')
    ).count()

    # Open Chats: Count jobs where the pro is still talking or has sent a quote
    open_chats_count = JobRequest.select().where(
        (JobRequest.pro == current_user) & (JobRequest.status.in_(['chatting', 'quoted']))
    ).count()

    completed_count = JobRequest.select().where(
        (JobRequest.pro == current_user) & (JobRequest.status == 'completed')
    ).count()
    # 2. Get the priority list (Anything that isn't finished yet)
    # We include 'completed' in the list but order by date so new stuff stays on top
    active_business = JobRequest.select().where(
        (JobRequest.pro == current_user) & 
        (JobRequest.status.in_(['chatting', 'hired', 'quoted', 'completed']))
    ).order_by(JobRequest.created_at.desc()).limit(10)

    return render_template('pro/dashboard.html', 
                           total_earnings=total_earnings,
                           active_count=active_count,
                           open_chats_count=open_chats_count,
                           active_business=active_business,
                           current_date=current_date,
                           completed_count = completed_count)
# Ensure upload folder exists
UPLOAD_FOLDER = 'static/uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

@pro_bp.route('/profile/setup', methods=['GET', 'POST'])
@login_required
@role_required('pro')
def profile_setup():
    if request.method == 'POST':
        # Business Information
        current_user.category = request.form.get('category')
        current_user.location = request.form.get('location')
        current_user.phone = request.form.get('phone')
        current_user.base_rate = request.form.get('base_rate')
        current_user.bio = request.form.get('bio')
        
        # Save profile picture if uploaded
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and file.filename != '':
                ext = file.filename.rsplit('.', 1)[1].lower()
                filename = secure_filename(f"avatar_{current_user.id}.{ext}")
                file.save(os.path.join(UPLOAD_FOLDER, filename))
                current_user.profile_pic = filename


        # Add this line to update the bio/business name from the form
        current_user.name = request.form.get('name')
        current_user.bio = request.form.get('bio')
        current_user.phone = request.form.get('phone')
        current_user.category = request.form.get('category')
        current_user.location = request.form.get('location')
        current_user.base_rate = request.form.get('base_rate')
        category = Category.select(Category.id).where(Category.name == request.form.get('category'))
        print(f'--- {category}')
        # ONLY save these specific fields to avoid the Category error
        current_user.save(only=[User.profile_pic, User.username, User.bio,User.location,User.phone,User.base_rate])
        
        flash("Business profile updated!", "success")
        return redirect(url_for('pro.profile_setup'))

    # GET request logic
    categories = Category.select()
    return render_template('pro/profile_setup.html', categories=categories)

@pro_bp.route('/portfolio', methods=['GET', 'POST'])
@login_required
def portfolio():
    if current_user.role != 'pro':
        return redirect(url_for('customer.explore'))

    if request.method == 'POST':
        if 'gallery' in request.files:
            files = request.files.getlist('gallery')
            for file in files:
                if file and file.filename != '':
                    # Ensure filename is safe and unique
                    ext = file.filename.rsplit('.', 1)[1].lower()
                    filename = secure_filename(f"work_{current_user.id}_{os.urandom(4).hex()}.{ext}")
                    file.save(os.path.join(UPLOAD_FOLDER, filename))
                    
                    # Create the database record for the image
                    GalleryImage.create(user=current_user, filename=filename)
            
            flash("Portfolio updated successfully!", "success")
            return redirect(url_for('pro.portfolio'))

    return render_template('pro/portfolio.html')

@pro_bp.route('/portfolio/delete/<int:image_id>', methods=['POST'])
@login_required
@role_required('pro')
def delete_gallery_image(image_id):
    image = GalleryImage.get_by_id(image_id)
    
    # Security check: ensure the image belongs to the current user
    if image.user_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for('pro.profile_setup'))

    # Delete the physical file (Optional but recommended)
    try:
        os.remove(os.path.join(UPLOAD_FOLDER, image.filename))
    except OSError:
        pass # File already gone or path error

    image.delete_instance()
    flash("Image removed from portfolio.", "success")
    return redirect(url_for('pro.profile_setup'))

    
@pro_bp.route('/set-quote/<int:job_id>', methods=['POST'])
@login_required
def set_quote(job_id):
    job = JobRequest.get_by_id(job_id)
    amount = request.form.get('amount')
    title = request.form.get('title')

    # Now we update the record from 'chatting' to 'quoted'
    job.total_amount = amount
    job.title = title
    job.status = 'quoted'
    job.save()

    # Automatically send a system message
    Message.create(
        job_request=job,
        sender=current_user,
        content=f"PROPOSAL: I have set the project price to GHC₵{amount}. Please review and accept to begin.",
        is_system_message=True # If you have this field
    )

    flash("Quote sent to customer!", "success")
    return redirect(url_for('chat.view_chat', job_id=job.id))


@pro_bp.route('/job/approve/<int:job_id>', methods=['POST'])
@login_required
@role_required('pro')
def create_official_job(job_id):
    job = get_by_id_or_404(JobRequest, job_id)
    price = request.form.get('final_price')
    
    if price:
        job.total_amount = price
        job.status = 'quoted'
        job.save()
        
        # FIXED: Changed 'job=' to 'job_request='
        Message.create(
            job_request=job,
            sender=current_user,
            content=f"OFFICIAL QUOTE: I have set the price to GHS {price}. You can now pay to start the project."
        )
        
    return redirect(url_for('chat.view_chat', job_id=job.id))


@pro_bp.route('/customer-requests')
@login_required
@role_required('pro')
def customer_requests():
    # Simply get all conversations for this Pro, newest first
    requests = JobRequest.select().where(
        JobRequest.pro == current_user
    ).order_by(JobRequest.created_at.desc())
    
    return render_template('pro/customer_requests.html', requests=requests)

@pro_bp.route('/customer/profile/<int:user_id>')
@login_required
@role_required('pro')
def view_customer_profile(user_id):
    # Fetch the customer, ensuring they actually have the 'customer' role
    customer = User.get_or_none((User.id == user_id) & (User.role == 'customer'))
    
    if not customer:
        abort(404)
        
    # Get total projects this customer has started (optional but professional)
    project_count = JobRequest.select().where(JobRequest.customer == customer).count()
    
    return render_template('pro/customer_profile.html', 
                           customer=customer, 
                           project_count=project_count)

@pro_bp.route('/<int:user_id>')
@role_required('pro')
@login_required
def view_profile(user_id):
    pro = get_by_id_or_404(User,user_id)
    if pro.role != 'pro':
        abort(404)
        
    # Get their portfolio images
    portfolio = GalleryImage.select().where(GalleryImage.user == pro).order_by(GalleryImage.created_at.desc())
    
    # Get their completion count for social proof
    completed_jobs = JobRequest.select().where(
        (JobRequest.pro == pro) & (JobRequest.status == 'completed')
    ).count()

    return render_template('pro/pro_profile.html',title = 'Pro Profile', pro=pro, portfolio=portfolio, completed_count=completed_jobs)

@pro_bp.route('/wallet')
@login_required
def wallet_view():
    # Fetch transactions, newest first
    transactions = Transaction.select().where(
        Transaction.user == current_user
    ).order_by(Transaction.timestamp.desc())
    
    return render_template('pro/wallet.html', transactions=transactions)



@pro_bp.route('/wallet/withdraw', methods=['POST'])
@login_required
def request_withdrawal():
    amount = float(request.form.get('amount'))
    momo_number = request.form.get('momo_number')
    network = request.form.get('network')

    if amount > current_user.balance:
        flash('Insufficient balance.', 'danger')
        return redirect(url_for('view_wallet'))

    try:
        with db.atomic():
            # 1. Deduct from User
            current_user.balance -= amount
            current_user.save()

            # 2. Create Transaction Record
            Transaction.create(
                user=current_user.id,
                amount=amount,
                type='withdraw',
                status='pending',
                momo_number=momo_number,
                network=network
            )
        
        flash('Withdrawal request submitted successfully.', 'success')
    except Exception as e:
        # If anything fails inside the 'with', Peewee rolls back automatically
        flash('Transaction failed. Please try again.', 'danger')

    return redirect(url_for('view_wallet'))

@pro_bp.route('/accept-inquiry/<int:job_id>', methods=['POST'])
@login_required
@role_required('pro')
def accept_inquiry(job_id):
    job = get_by_id_or_404(JobRequest,job_id)
    
    if job.pro == current_user:
        job.status = 'accepted'
        job.save()
        
        # Send an automated intro message so the chat isn't empty
        Message.create(
            job=job,
            sender=current_user,
            content=f"Hi {job.customer.username}, I've accepted your inquiry! How can I help you today?"
        )
        
        return redirect(url_for('chat.view_chat', job_id=job.id))
    
    return "Action denied", 403

@pro_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@role_required('pro')
def settings():
    email_otp_key = f"otp_data_{current_user.id}"
    phone_otp_key = f"phone_otp_{current_user.id}"

    if request.method == 'POST':
        action = request.form.get('action')

        # --- 1. EMAIL OTP (Mailtrap) ---
        if action == 'send_otp':
            otp = str(random.randint(100000, 999999))
            session[email_otp_key] = {
                'code': otp,
                'expiry': (datetime.now() + timedelta(minutes=10)).timestamp()
            }
            session.modified = True 

            try:
                client = mt.MailtrapClient(token="a58e652c3798105186e3d498f2bdb88e")
                html_content = render_template('pro/otp_email.html', otp=otp, user=current_user)
                mail_obj = mt.Mail(
                    sender=mt.Address(email="hello@demomailtrap.co", name="IKS1 Verification"),
                    to=[mt.Address(email=current_user.email)],
                    subject="Your Email Verification Code",
                    html=html_content,
                )
                client.send(mail_obj)
                flash("A 6-digit code has been sent to your email.", "info")
            except Exception as e:
                session.pop(email_otp_key, None)
                flash("Failed to send email. Check connection.", "danger")
            
            return redirect(url_for('pro.settings'))

        elif action == 'verify_otp':
            user_otp = request.form.get('otp_input')
            otp_data = session.get(email_otp_key)

            if otp_data and datetime.now().timestamp() <= otp_data.get('expiry'):
                if user_otp == otp_data.get('code'):
                    current_user.email_verified = True
                    current_user.check_and_verify() # <--- Trigger Auto-Verify
                    session.pop(email_otp_key, None)
                    flash("Email verified successfully!", "success")
                else:
                    flash("Invalid code.", "danger")
            else:
                flash("Code expired or session lost.", "warning")
            return redirect(url_for('pro.settings'))

        # --- 2. PHONE OTP (Arkesel SMS) ---
        elif action == 'send_phone_otp':
            if not current_user.phone:
                flash("Please add a phone number first.", "warning")
                return redirect(url_for('pro.settings'))

            otp = str(random.randint(100000, 999999))
            session[phone_otp_key] = {
                'code': otp,
                'expiry': (datetime.now() + timedelta(minutes=5)).timestamp()
            }
            session.modified = True

            try:
                # Format phone: 054... -> 23354...
                phone = current_user.phone
                if phone.startswith('0'): phone = '233' + phone[1:]
                
                headers = {'api-key': 'YOUR_ARKESEL_KEY'} # Replace with actual key
                payload = {
                    'sender': 'Arkesel', # Use 'Arkesel' until 'IKS1' is approved
                    'message': f"Your IKS1 verification code is {otp}",
                    'recipients': [phone]
                }
                requests.post("https://sms.arkesel.com/api/v2/sms/send", json=payload, headers=headers)
                flash("SMS verification code sent!", "info")
            except Exception:
                flash("Failed to send SMS.", "danger")
            return redirect(url_for('pro.settings'))

        elif action == 'verify_phone_otp':
            entered_code = request.form.get('phone_otp_input')
            otp_data = session.get(phone_otp_key)

            if otp_data and entered_code == otp_data['code']:
                current_user.phone_verified = True
                current_user.check_and_verify() # <--- Trigger Auto-Verify
                session.pop(phone_otp_key, None)
                flash("Phone number verified!", "success")
            else:
                flash("Invalid SMS code.", "danger")
            return redirect(url_for('pro.settings'))

        # --- 3. KYC SUBMISSION (With File Upload) ---
        elif action == 'submit_kyc':
            file = request.files.get('kyc_doc')
            if file and file.filename != '':
                ext = file.filename.rsplit('.', 1)[1].lower()
                filename = secure_filename(f"kyc_{current_user.id}_{os.urandom(3).hex()}.{ext}")
                
                # Ensure kyc subfolder exists
                kyc_path = os.path.join(current_app.root_path, 'static/uploads/kyc')
                if not os.path.exists(kyc_path): os.makedirs(kyc_path)
                
                file.save(os.path.join(kyc_path, filename))
                
                # Update User
                current_user.kyc_status = 'pending'
                # current_user.kyc_document = filename # Uncomment if you have this field
                current_user.save()
                flash("KYC Documents submitted for review.", "info")
            else:
                flash("Please select a document to upload.", "warning")
            return redirect(url_for('pro.settings'))

        # --- 4. PROFILE UPDATES ---
        elif action == 'update_profile':
            current_user.phone = request.form.get('phone')
            current_user.save()
            flash("Phone number updated.", "success")
            return redirect(url_for('pro.settings'))

    return render_template('pro/settings.html')