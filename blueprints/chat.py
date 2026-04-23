import datetime
from flask import Blueprint, request, redirect, url_for, render_template, abort
from flask_login import current_user, login_required
from models import Message, JobRequest, User
from iknow_utils import role_required

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/new/<int:pro_id>')
@login_required
def new_chat(pro_id):
    pro = User.get_by_id(pro_id)
    # We pass 'job=None' so the template knows it's a fresh start
    return render_template('chat/view_chat.html', job=None, other_user=pro, is_new=True)

@chat_bp.route('/send/<int:target_id>', methods=['POST'])
@login_required
def send_message(target_id):
    content = request.form.get('content')
    if not content or content.strip() == "":
        return redirect(request.referrer)

    # Determine if target_id is a Job ID or a Pro ID
    # We check if we are coming from a 'new_chat' or an existing 'view_chat'

    try:
        job = JobRequest.get_by_id(target_id)
        
        # BLOCKER: Check if the job is closed (Completed + Reviewed)
        if job.status == 'completed' and job.review.exists():
            flash("This conversation is closed.", "warning")
            return redirect(url_for('chat.view_chat', job_id=job.id))
            
    except JobRequest.DoesNotExist:
        pass

    is_new_chat = request.args.get('new') == 'true'
    if is_new_chat:
        # --- LAZY CREATION HAPPENS HERE ---
        pro = User.get_by_id(target_id)
        job = JobRequest.create(
            customer=current_user,
            pro=pro,
            title=f"Project with {pro.username}",
            description="Discussion started.",
            status='chatting',
            total_amount=0
        )
    else:
        try:
            job = JobRequest.get_by_id(target_id)
        except JobRequest.DoesNotExist:
            abort(404)

    # Save the message
    Message.create(
        job_request=job,
        sender=current_user,
        content=content.strip(),
        timestamp=datetime.datetime.now()
    )
    
    return redirect(url_for('chat.view_chat', job_id=job.id))

@chat_bp.route('/view/<int:job_id>')
@login_required
def view_chat(job_id):
    try:
        job = JobRequest.get_by_id(job_id)
        
        # Security Check
        if current_user != job.pro and current_user != job.customer:
            abort(403)

        # --- GHOST CLEANUP LOGIC ---
        # If this is a 'chatting' session and the user is LEAVING without any messages,
        # we could delete it. But a safer way is to check if it's old and empty.
        messages = Message.select().where(Message.job_request == job).order_by(Message.timestamp.asc())
        
        # If the Pro opens an empty chat, it's just an inquiry. 
        # The 'Job' only becomes 'Official' once the status changes to 'quoted' or 'active'.
        # ---------------------------

        # Mark others' messages as read
        Message.update(is_read=True).where(
            (Message.job_request == job) & 
            (Message.sender != current_user)
        ).execute()

        return render_template('chat/view_chat.html', job=job, messages=messages)
        
    except JobRequest.DoesNotExist:
        abort(404)