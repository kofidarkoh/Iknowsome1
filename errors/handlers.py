from flask import Blueprint, render_template

errors_bp = Blueprint('errors', __name__, template_folder='templates')

@errors_bp.app_errorhandler(404)
def error_404(error):
    return render_template('errors/404.html'), 404

@errors_bp.app_errorhandler(500)
def error_500(error):
    return render_template('errors/500.html'), 500
