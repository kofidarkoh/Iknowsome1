from functools import wraps
import datetime
from flask import Flask, render_template,request, abort, session, url_for,jsonify
from flask_login import LoginManager,current_user, login_required
from blueprints.auth import auth_bp
from blueprints.customer import customer_bp
from blueprints.pro import pro_bp
from blueprints.admin import admin_bp
from blueprints.chat import chat_bp
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from models import User, init_db, Message, JobRequest, db, fn, Transaction


app = Flask(__name__)
app.config['SECRET_KEY'] = 'iknowsomeone-2026-key'

# Register it after app = Flask(__name__)
app.register_blueprint(auth_bp)
app.register_blueprint(customer_bp, url_prefix='/customer')
app.register_blueprint(pro_bp, url_prefix='/pro')
app.register_blueprint(chat_bp, url_prefix='/chat')
app.register_blueprint(admin_bp, url_prefix='/admin')

# SMTP Configuration  2848aa22660dbd9b7e203fb167b93d2c
app.config["MAIL_SERVER"] = "smtp-relay.brevo.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USERNAME"] = "a8aed0001@smtp-brevo.com"
app.config["MAIL_PASSWORD"] = "xsmtpsib-26e80dfc3107b80070810f4b516907973f4fce0402d978930793947a4be9a547-LHn9ZapSowtC64l0"
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USE_SSL"] = False

mail = Mail(app)
ts = URLSafeTimedSerializer(app.config["SECRET_KEY"])

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

@login_manager.user_loader
def load_user(user_id):
    return User.get_or_none(User.id == user_id)



@app.before_request
def update_user_activity():
    """Update last_active on every page request"""
    if current_user and current_user.is_authenticated:
        User.update(
            last_active=datetime.datetime.now()
        ).where(
            User.id == current_user.id
        ).execute()

@app.route('/heartbeat', methods=['POST'])
@login_required
def heartbeat():
    from playhouse.shortcuts import model_to_dict
    import datetime
    
    try:
        with db.atomic():
            rows_updated = User.update(
                last_active=datetime.datetime.now()
            ).where(User.id == current_user.id).execute()
            
        return jsonify({'status': 'ok', 'timestamp': datetime.datetime.now().isoformat()})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500



def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role != role:
                abort(403) # "Forbidden" error
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.before_request
def _db_connect():
    if db.is_closed():
        db.connect()

@app.teardown_request
def _db_close(exc):
    if not db.is_closed():
        db.close()


@app.context_processor
def inject_global_data():
    from models import JobRequest # Ensure this matches your model import path
    return dict(JobRequest=JobRequest)

@app.context_processor
def inject_models():
    return dict(JobRequest=JobRequest)
        
@app.context_processor
def inject_notifications():
    if current_user.is_authenticated:
        from models import Message, JobRequest
        
        try:
            # We count the number of UNIQUE JobRequests that have unread messages
            unread_conversations = (Message
                .select(Message.job_request) # Select the foreign key
                .join(JobRequest)
                .where(
                    (Message.is_read == False) & 
                    (Message.sender != current_user) & 
                    ((JobRequest.pro == current_user) | (JobRequest.customer == current_user))
                )
                .distinct() # Only count each job once
                .count())
            
            return {'notif_count': unread_conversations}
        except Exception as e:
            print(f"Notification error: {e}")
            return {'notif_count': 0}
            
    return {'notif_count': 0}

@app.route('/')
def home():
    # Randomly select 3 Pros who have a profile picture
    featured_pros = User.select().where(
        (User.role == 'pro'),
        (User.profile_pic != None)
    ).order_by(fn.Random()).limit(3) # This ensures a different set on every refresh
    
    # Live stats
    stats = {
        'total_pros': User.select().where(User.role == 'pro').count(),
        'projects': JobRequest.select().where(JobRequest.status == 'completed').count()
    }
    
    return render_template('index.html', pros=featured_pros, stats=stats)

@app.route('/about')
def about():
    return render_template('about.html')

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403




if __name__ == '__main__':
    init_db()
    app.run(debug=True)
