from functools import wraps
from flask import abort
from models import JobRequest as Job, Transaction
from flask_login import current_user

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role != role:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def get_by_id_or_404(model_cls, obj_id):
    try:
        return model_cls.get_by_id(obj_id)
    except model_cls.DoesNotExist:
        abort(404)

def process_payment(job_id):
    job = Job.get_by_id(job_id)
    customer = job.customer
    pro = job.pro
    amount = job.total_amount

    if customer.balance < amount:
        return False

    try:
        with db.atomic():
            # Update balances
            customer.balance -= amount
            pro.balance += amount
            
            customer.save()
            pro.save()

            # Record for Customer (negative)
            Transaction.create(user=customer, amount=-amount, type='payment')
            
            # Record for Pro (positive)
            Transaction.create(user=pro, amount=amount, type='payment')

            # Update Job Status
            job.status = 'hired'
            job.save()
            
        return True
    except:
        return False