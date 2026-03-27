# logger.py
import logging
from functools import wraps
from flask import request, session, g
import sqlite3
import json
from datetime import datetime
import os
import sys

# File-based logging for system events (optional)
logging.basicConfig(
    filename='church_app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class ActivityLogger:
    """Main logger class for user activities"""
    
    def __init__(self, app=None):
        self.app = app
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize with Flask app"""
        app.activity_logger = self
        # Register before/after request handlers
        app.before_request(self.log_request_start)
        app.after_request(self.log_request_end)
    
    def get_db_path(self):
        """Get database path from app config"""
        from app import DB_PATH  # Import your DB_PATH
        return DB_PATH
    
    def log_event(self, action, details=None, status="success", user_id=None):
        """Log an event to the database"""
        try:
            conn = sqlite3.connect(self.get_db_path())
            cursor = conn.cursor()
            
            # Get user info from session if available
            if user_id is None:
                user_id = session.get('user_id')
            
            username = session.get('username', 'anonymous')
            
            # Get request information
            ip = request.remote_addr if request else 'N/A'
            user_agent = request.headers.get('User-Agent', 'N/A') if request else 'N/A'
            endpoint = request.endpoint if request else 'N/A'
            method = request.method if request else 'N/A'
            
            # Convert details to JSON string if it's a dict
            if details and isinstance(details, dict):
                details = json.dumps(details)
            
            cursor.execute('''
                INSERT INTO activity_logs 
                (user_id, username, action, details, ip_address, user_agent, endpoint, method, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, username, action, details, ip, user_agent, endpoint, method, status))
            
            conn.commit()
            conn.close()
            
            # Also log to file for system debugging
            logging.info(f"User: {username} - Action: {action} - IP: {ip}")
            
        except Exception as e:
            logging.error(f"Failed to log event: {str(e)}")
    
    def log_request_start(self):
        """Log when a request starts (decorator)"""
        if request.endpoint and not request.endpoint.startswith('static'):
            self.log_event(
                action="request_start",
                details={"url": request.url, "args": dict(request.args)},
                status="info"
            )
    
    def log_request_end(self, response):
        """Log when a request ends"""
        if request.endpoint and not request.endpoint.startswith('static'):
            status_code = response.status_code
            if status_code >= 400:
                self.log_event(
                    action="request_error",
                    details={"url": request.url, "status": status_code},
                    status="error"
                )
        return response
    
    def log_login(self, username, success=True):
        """Log login attempts"""
        self.log_event(
            action="login",
            details={"username": username},
            status="success" if success else "failed"
        )
    
    def log_logout(self, username):
        """Log logout"""
        self.log_event(
            action="logout",
            details={"username": username},
            status="success"
        )
    
    def log_data_entry(self, record_data):
        """Log data entry submissions"""
        self.log_event(
            action="data_entry",
            details=record_data,
            status="success"
        )
    
    def log_expense_update(self, expense_id, changes):
        """Log expense updates"""
        self.log_event(
            action="expense_update",
            details={"expense_id": expense_id, "changes": changes},
            status="success"
        )
    
    def log_project_creation(self, project_name):
        """Log new project creation"""
        self.log_event(
            action="project_create",
            details={"project_name": project_name},
            status="success"
        )
    
    def log_error(self, error_message, endpoint=None):
        """Log errors"""
        self.log_event(
            action="error",
            details={"error": error_message, "endpoint": endpoint},
            status="error"
        )

# Decorator for automatic function logging
def log_activity(action_name=None):
    """Decorator to automatically log function calls"""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # Get logger instance
            from flask import current_app
            logger = getattr(current_app, 'activity_logger', None)
            
            action = action_name or f.__name__
            
            try:
                # Execute the function
                result = f(*args, **kwargs)
                
                # Log success
                if logger:
                    logger.log_event(
                        action=action,
                        details={"args": str(args), "kwargs": str(kwargs)},
                        status="success"
                    )
                return result
                
            except Exception as e:
                # Log error
                if logger:
                    logger.log_event(
                        action=action,
                        details={"error": str(e), "args": str(args)},
                        status="error"
                    )
                raise
        return wrapped
    return decorator