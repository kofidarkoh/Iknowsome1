import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import User
from werkzeug.security import generate_password_hash

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role') # 'customer' or 'pro'

        # Basic check if user exists
        if User.get_or_none(User.email == email):
            flash('Email already registered', 'warning')
            return redirect(url_for('auth.register'))

        new_user = User(username=username, email=email, role=role)
        new_user.set_password(password)
        new_user.save()
        if new_user.role == 'pro':
            login_user(new_user)
            return redirect(url_for('pro.dashboard'))
        if new_user.role == 'customer':
            login_user(new_user)
            return redirect(url_for('customer.dashboard'))
    return render_template('auth/register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.get_or_none(User.email == email)
        if user and user.check_password(password):
        	login_user(user)
        	# Redirect based on role
        	if user.role == 'admin':
        		return redirect(url_for('admin.dashboard'))
        	elif user.role == 'pro':
        		return redirect(url_for('pro.dashboard'))
        	else:
        		return redirect(url_for('customer.dashboard'))
        flash('Invalid email or password', 'danger')
    return render_template('auth/login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    if current_user.role =='admin':
        logout_user()
        return redirect(url_for('admin.login'))
    logout_user()
    return redirect(url_for('auth.login'))