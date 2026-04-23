from flask import Blueprint, render_template, redirect, url_for, request, flash,abort
from flask_login import login_user, logout_user, login_required, current_user
from models import User, JobRequest,fn, db, SystemSetting, Transaction
from werkzeug.security import generate_password_hash, check_password_hash
from iknow_utils import role_required

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/dashboard')
@login_required
def dashboard():
    if current_user.role != 'admin':
        abort(403)
        
    stats = {
        'total_users': User.select().count(),
        'total_pros': User.select().where(User.role == 'pro').count(),
        'active_jobs': JobRequest.select().where(JobRequest.status != 'completed').count(),
        'total_revenue': JobRequest.select(fn.SUM(JobRequest.total_amount)).where(JobRequest.status == 'hired').scalar() or 0
    }
    
    # NEW: Fetch pending transactions for the "Action Required" section
    pending_transactions = (Transaction
                           .select(Transaction, User)
                           .join(User)
                           .where(Transaction.status == 'pending')
                           .order_by(Transaction.timestamp.asc()))
    
    recent_users = User.select().order_by(User.id.desc()).limit(5)
    recent_jobs = JobRequest.select().order_by(JobRequest.id.desc()).limit(5)
    
    return render_template('admin/dashboard.html', 
                           stats=stats, 
                           users=recent_users, 
                           jobs=recent_jobs, 
                           pending_transactions=pending_transactions)


@admin_bp.route('/approve-transaction/<int:txn_id>', methods=['POST'])
@login_required
def approve_transaction(txn_id):
    if current_user.role != 'admin':
        abort(403)
        
    try:
        with db.atomic():
            txn = Transaction.get_by_id(txn_id)
            user = txn.user
            
            if txn.status == 'pending':
                if txn.type == 'deposit':
                    # For manual deposits, we add the money now that we've seen it in our Momo
                    user.balance += txn.amount
                    user.save()
                    txn.status = 'completed'
                    flash(f"Approved deposit of ₵{txn.amount} for {user.username}", "success")
                
                elif txn.type == 'withdraw':
                    # For withdrawals, money was deducted when they requested it.
                    # Approval simply means "I have manually sent the Momo transfer."
                    txn.status = 'completed'
                    flash(f"Withdrawal for {user.username} marked as completed.", "success")
                
                txn.save()
            else:
                flash("Transaction already processed.", "info")
                
    except Transaction.DoesNotExist:
        flash("Transaction not found.", "danger")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
        
    return redirect(url_for('admin.dashboard'))



@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated and current_user.role == 'admin':
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.get_or_none(User.email == email)
        
        if user and check_password_hash(user.password, password):
            if user.role == 'admin':
                login_user(user)
                return redirect(url_for('admin.dashboard'))
            else:
                flash('Access Denied: Restricted Area.', 'danger')
        else:
            flash('Invalid credentials.', 'danger')            
    return render_template('admin/login.html')

@admin_bp.route('/users')
@login_required
@role_required('admin')
def user_management():
    search = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    limit = 10  # Number of users per page
    
    query = User.select().order_by(User.id.desc())
    
    if search:
        query = query.where((User.username.contains(search)) | (User.email.contains(search)))
    
    users = query.paginate(page, limit)
    total_count = query.count()
    has_more = total_count > (page * limit)

    if request.headers.get('HX-Request'):
        return render_template('admin/partials/user_rows.html', 
                               users=users, page=page, has_more=has_more, search=search)
    
    return render_template('admin/users.html', 
                           users=users, page=page, has_more=has_more, search=search)

@admin_bp.route('/users/edit/<int:user_id>')
def get_edit_modal(user_id):
    user = User.get_by_id(user_id)
    return render_template('admin/partials/modal_edit.html', user=user)

@admin_bp.route('/users/modal/verify/<int:user_id>')
@login_required
@role_required('admin')
def get_verify_modal(user_id):
    try:
        user = User.get_by_id(user_id)
        # We serve the clean partial we designed earlier
        return render_template('admin/partials/modal_verify.html', user=user)
    except User.DoesNotExist:
        return "User not found", 404

@admin_bp.route('/users/update/<int:user_id>', methods=['POST'])
def update_user(user_id):
    user = User.get_by_id(user_id)
    user.username = request.form.get('username')
    user.role = request.form.get('role')
    user.save()
    
    # After update, HTMX replaces the specific row
    return render_template('admin/partials/user_rows.html', users=[user])


@admin_bp.route('/users/delete/<int:user_id>', methods=['DELETE'])
@login_required
@role_required('admin')
def delete_user(user_id):
    try:
        user = User.get_by_id(user_id)
        
        # Prevent admin from deleting themselves
        if user.id == current_user.id:
            return "You cannot delete your own account.", 400
            
        user.delete_instance()
        
        # Return empty string with 200 OK
        # HTMX will see the empty response and remove the row
        return "", 200
    except User.DoesNotExist:
        return "User not found", 404


@admin_bp.route('/users/quick-verify/<int:user_id>', methods=['POST'])
@login_required
def quick_verify(user_id):
    if current_user.role != 'admin':
        return "Unauthorized", 403
        
    try:
        # Use Peewee's get() for cleaner error handling
        user = User.get(User.id == user_id)
        
        # Manually approve everything
        user.email_verified = True
        user.phone_verified = True
        user.kyc_status = 'verified'
        user.full_verified = True 
        user.save()
        
        # HTMX logic: returning an empty string removes the target element
        # We also send a header so you can trigger a toast notification if you want
        return "", 200
        
    except User.DoesNotExist:
        return '<span class="text-danger">User not found</span>', 404
    except Exception as e:
        return f'<span class="text-danger">Error: {str(e)}</span>', 500


@admin_bp.route('/users/verification')
@login_required
def verification_manager():
    if current_user.role != 'admin':
        abort(403)
    
    query = User.select().where(
        (User.email_verified == False) | 
        (User.phone_verified == False) | 
        (User.full_verified == False)
    ).order_by(User.id.desc())

    # Check if this is an HTMX request for the table only (searching/filtering)
    if request.headers.get('HX-Request'):
        search = request.args.get('search', '')
        if search:
            query = query.where(User.username.contains(search) | User.email.contains(search))
        return render_template('admin/partials/_verify_table.html', unverified_users=query)

    return render_template('admin/verification_page.html', unverified_users=query)

@admin_bp.route('/users/edit-verification/<int:user_id>')
@login_required
def edit_user_verification(user_id):
    user = User.get_by_id(user_id)
    return render_template('admin/partials/_edit_user_row.html', user=user)

@admin_bp.route('/users/update-verification/<int:user_id>', methods=['POST'])
@login_required
def update_user_verification(user_id):
    user = User.get_by_id(user_id)
    
    # Update based on form switches
    user.email_verified = 'email_v' in request.form
    user.phone_verified = 'phone_v' in request.form
    
    # If both are checked now, maybe auto-verify KYC/Full
    if user.email_verified and user.phone_verified:
        user.kyc_status = 'verified'
        user.full_verified = True
        
    user.save()
    
    # If the user is now fully verified, they should disappear from the queue
    if user.full_verified:
        return ""
    
    # Otherwise, return the normal row again showing updated icons
    return render_template('admin/partials/_verify_table.html', unverified_users=[user])


@admin_bp.route('/kyc')
@login_required
@role_required('admin')
def kyc_list():
    # Only show users who have submitted but are not yet verified
    pending_kyc = User.select().where(User.kyc_status == 'submitted').order_by(User.id.desc())
    
    if request.headers.get('HX-Request'):
        return render_template('admin/partials/kyc_rows.html', pending_users=pending_kyc)
    
    return render_template('admin/kyc_list.html', pending_users=pending_kyc)

@admin_bp.route('/approve-kyc/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def approve_kyc(user_id):
    user = User.get_by_id(user_id)
    user.kyc_status = 'verified'
    user.check_and_verify() 
    user.save()
    
    # Send trigger to update sidebar badge automatically
    response = make_response("", 200)
    response.headers['HX-Trigger'] = 'kycUpdated'
    return response

@admin_bp.route('/kyc/reject-modal/<int:user_id>')
def get_reject_modal(user_id):
    user = User.get_by_id(user_id)
    return render_template('admin/partials/modal_reject.html', user=user)

@admin_bp.route('/kyc/reject/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def reject_kyc(user_id):
    user = User.get_by_id(user_id)
    reason = request.form.get('reason')
    
    user.kyc_status = 'rejected'
    # user.kyc_notes = reason # If you added this field
    user.save()
    
    response = make_response("", 200)
    response.headers['HX-Trigger'] = 'kycUpdated'
    return response

@admin_bp.route('/kyc/reject-modal/<int:user_id>')
@login_required
@role_required('admin')
def getkyc_reject_modal(user_id):
    user = User.get_by_id(user_id)
    # Serves the KYC-specific rejection form
    return render_template('admin/partials/modal_reject_kyc.html', user=user)



@admin_bp.route('/kyc/count')
@login_required
def get_kyc_count():
    count = User.select().where(User.kyc_status == 'submitted').count()
    # Return nothing if count is 0 to keep the sidebar clean
    return str(count) if count > 0 else ""

# def create_master_admin():
#     admin_email = "admin@iknowsome1.com"
    
#     # Check if exists
#     if not User.select().where(User.email == admin_email).exists():
#         User.create(
#             username="MasterAdmin",
#             email=admin_email,
#             password=generate_password_hash("admin"),
#             role="admin"
#         )
#         print("Admin created successfully.")
#     else:
#         print("Admin already exists.")

# create_master_admin()


from werkzeug.security import generate_password_hash, check_password_hash

@admin_bp.route('/settings')
@login_required
@role_required('admin')
def settings():
    return render_template('admin/settings.html')

@admin_bp.route('/settings/update-profile', methods=['POST'])
@login_required
def update_settings_profile():
    current_user.username = request.form.get('username')
    current_user.email = request.form.get('email')
    current_user.save()
    return "", 200

@admin_bp.route('/settings/update-password', methods=['POST'])
@login_required
def update_settings_password():
    current_pw = request.form.get('current_password')
    new_pw = request.form.get('new_password')
    confirm_pw = request.form.get('confirm_password')

    # 1. Check current password
    if not check_password_hash(current_user.password, current_pw):
        return '<div class="alert alert-danger small py-2 rounded-3">Current password is incorrect.</div>', 200

    # 2. Check match
    if new_pw != confirm_pw:
        return '<div class="alert alert-danger small py-2 rounded-3">New passwords do not match.</div>', 200

    # 3. Save
    current_user.password = generate_password_hash(new_pw)
    current_user.save()
    
    return '<div class="alert alert-success small py-2 rounded-3">Password updated successfully!</div>', 200


@admin_bp.route('/system-settings')
@login_required
@role_required('admin')
def system_settings():
    # Fetch all settings or defaults
    settings = {s.key: s.value for s in SystemSetting.select()}
    return render_template('admin/system_settings.html', settings=settings)

@admin_bp.route('/system-settings/toggle', methods=['POST'])
@login_required
def toggle_setting():
    key = request.form.get('key')
    # Toggle logic: if "true" set to "false", etc.
    setting, created = SystemSetting.get_or_create(key=key, defaults={'value': 'false'})
    setting.value = 'true' if setting.value == 'false' else 'false'
    setting.save()
    
    # Return the new status as a simple badge/text
    return "Enabled" if setting.value == 'true' else "Disabled"