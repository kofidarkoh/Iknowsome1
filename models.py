from peewee import *
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import datetime, os

base_dir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(base_dir, 'iknowsomeone.db')


db = SqliteDatabase(db_path, pragmas={
    'journal_mode': 'wal',    # Allows simultaneous read/write
    'cache_size': -1024 * 64, # 64MB cache
    'foreign_keys': 1,        # Enforce relationships
    'ignore_check_constraints': 0,
    'synchronous': 1          # Normal/Fast
})

class Category(Model):
    name = CharField(unique=True) # e.g., "Plumbing", "Graphic Design"
    icon = CharField(default="bi-briefcase") # Bootstrap icon name
    
    class Meta:
        database = db

class User(UserMixin, Model):
    name = CharField(null=True)
    username = CharField(unique=True)
    email = CharField(unique=True)
    email_verified = BooleanField(default=False)
    kyc_status = CharField(default='unverified')
    password = CharField()
    bio = TextField(unique=True)
    role = CharField(default='customer') # admin, pro, customer
    last_active = DateTimeField(default=datetime.datetime.now)
    balance = DecimalField(default=0.00, max_digits=10, decimal_places=2)
    # Optional: track money that hasn't been released yet for pros
    pending_balance = DecimalField(default=0.00, max_digits=10, decimal_places=2)
    # Pro Details
    category = ForeignKeyField(Category, backref='pros', null=True)
    location = CharField(null=True) # e.g., "Kumasi", "Accra - East Legon"
    phone = CharField(null=True) # For Arkesel SMS alerts
    phone_verified = BooleanField(default = False)
    profile_pic = CharField(default='default_pro.png')
    full_verified = BooleanField(default=False) # The "Gold Shield" badge
    base_rate = DecimalField(decimal_places=2, null=True) # Starting price in GHS
    
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)

    def is_online(self): # User is online if active within last 2 minutes
    # Browser closed? No new requests = timestamp older than 2 minutes = offline
        if not self.last_active:
            return False
        now = datetime.datetime.now()
        delay = (now - self.last_active).total_seconds()
        return delay < 30  # 30 seconds


    def check_and_verify(self):
        """
        Checks if all trust requirements are met. 
        If yes, the profile becomes visible (is_verified = True).
        """
        requirements = [
            self.email_verified,
            self.phone_verified,
            self.kyc_status == 'verified'
        ]
        
        if all(requirements):
            self.full_verified = True
        else:
            self.full_verified = False
            
        self.save()
# --- THE JOB WORKFLOW MODELS ---  , on_delete ='CASCADE', on_update ='CASCADE'

class JobRequest(Model):
    customer = ForeignKeyField(User, backref='requests_made')
    pro = ForeignKeyField(User, backref='requests_received')
    title = TextField()
    description = TextField(null = True)
    # Financial Fields
    total_amount = DecimalField(max_digits=10, decimal_places=2, default=0.00) 
    status = CharField(default='inquiry') # 'inquiry', 'hired', 'completed'
    
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db

    def has_unread(self, user):
        from models import Message
        last_msg = Message.select().where(Message.job_request == self).order_by(Message.timestamp.desc()).first()
        if last_msg:
            return last_msg.sender != user and not last_msg.is_read
        return False

    def unread_msg_count(self, user):
        # We import Message here to avoid circular import issues
        from models import Message 
        return Message.select().where(
            (Message.job_request == self) & 
            (Message.sender != user) & 
            (Message.is_read == False)
        ).count()

class Transaction(Model):
    user = ForeignKeyField(User, backref='transactions')
    amount = FloatField()
    t_type = CharField() # 'deposit', 'withdraw', 'payment'
    status = CharField(default='completed') # 'pending', 'completed'
    momo_number = CharField(null=True)
    network = CharField(null=True)
    timestamp = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db

class Review(Model):
    # Link to the job (One review per job)
    job = ForeignKeyField(JobRequest, backref='review', unique=True)
    customer = ForeignKeyField(User, backref='reviews_given')
    pro = ForeignKeyField(User, backref='reviews_received')
    
    rating = IntegerField() # 1 to 5
    comment = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db


class GalleryImage(Model):
    user = ForeignKeyField(User, backref='gallery')
    filename = CharField()
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db

# --- THE MESSAGE WORKFLOW MODELS ---
class Message(Model):
    # Explicitly set the column_name to match what is in your DB
    job_request = ForeignKeyField(JobRequest, backref='messages', column_name='job_request_id')
    sender = ForeignKeyField(User, backref='messages')
    content = TextField()
    timestamp = DateTimeField(default=datetime.datetime.now)
    is_system_message = BooleanField(default = True)
    is_read = BooleanField(default=False)

# No changes needed to JobRequest for now, as we can check job.status

    class Meta:
        database = db


class Ticket(Model):
    customer = ForeignKeyField(User, backref='tickets')
    subject = CharField()
    message = TextField()
    category = CharField() # e.g., 'verification', 'technical', 'payment'
    status = CharField(default='open') # open, closed, pending
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        table_name = 'user_transactions'



class SystemSetting(Model):
    key = CharField(unique=True)
    value = TextField()
    category = CharField(default="general") # e.g., 'security', 'payments', 'maintenance'

    class Meta:
        database = db

def init_db():
    """Initialize database with automatic migration for new models and fields."""
    db.connect()
    
    # Get all model classes
    all_models = [Category, SystemSetting, Transaction, Review, User, Ticket, GalleryImage, JobRequest, Message]
    
    existing_tables = db.get_tables()
    
    for model in all_models:
        table_name = model._meta.table_name
        
        if table_name not in existing_tables:
            db.create_tables([model])
            print(f"✓ Created table: {table_name}")
        else:
            try:
                # 1. Get existing columns from SQLite
                cursor = db.execute_sql(f'PRAGMA table_info("{table_name}")')
                # column[1] is the name of the column in the actual database
                existing_columns = [row[1] for row in cursor.fetchall()]
                
                # 2. Get columns defined in the Peewee model
                # Use .column_name instead of .keys() to get 'user_id' instead of 'user'
                model_columns = {field.column_name: field_name for field_name, field in model._meta.fields.items()}
                
                # 3. Find columns that need to be added
                for col_db_name, field_obj_name in model_columns.items():
                    if col_db_name not in existing_columns:
                        field = model._meta.fields[field_obj_name]
                        
                        # Determine SQLite column type
                        if isinstance(field, (IntegerField, BooleanField)):
                            col_type = "INTEGER"
                        elif isinstance(field, (CharField, TextField)):
                            col_type = "TEXT"
                        elif isinstance(field, DateTimeField):
                            col_type = "DATETIME"
                        elif isinstance(field, (DecimalField, FloatField)):
                            col_type = "REAL"
                        elif isinstance(field, ForeignKeyField):
                            col_type = "INTEGER"
                        else:
                            col_type = "TEXT"
                        
                        # Build default clause
                        default_clause = "DEFAULT NULL"
                        if not field.null:
                            if field.default is not None:
                                if callable(field.default):
                                    # Specific check for common Peewee/Python callables
                                    if "now" in str(field.default):
                                        default_clause = "DEFAULT CURRENT_TIMESTAMP"
                                    else:
                                        try:
                                            val = field.default()
                                            default_clause = f"DEFAULT '{val}'" if isinstance(val, str) else f"DEFAULT {val}"
                                        except: default_clause = ""
                                else:
                                    val = field.default
                                    default_clause = f"DEFAULT '{val}'" if isinstance(val, str) else f"DEFAULT {val}"
                            else:
                                # Fallback defaults for NOT NULL columns
                                if col_type == "INTEGER": default_clause = "DEFAULT 0"
                                elif col_type == "TEXT": default_clause = "DEFAULT ''"

                        # 4. Execute the Alter Table
                        try:
                            alter_sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{col_db_name}" {col_type} {default_clause}'
                            db.execute_sql(alter_sql)
                            print(f"  → Added column '{col_db_name}' to '{table_name}'")
                        except Exception as e:
                            print(f"  ⚠ Failed to add '{col_db_name}': {e}")

                # 5. Handle missing indexes for Foreign Keys
                cursor = db.execute_sql(f'PRAGMA index_list("{table_name}")')
                existing_indexes = [row[1] for row in cursor.fetchall()]
                
                for field_name, field in model._meta.fields.items():
                    if isinstance(field, ForeignKeyField):
                        idx_name = f"{table_name}_{field.column_name}"
                        if idx_name not in existing_indexes:
                            db.execute_sql(f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{table_name}" ("{field.column_name}")')
                            print(f"  → Created index '{idx_name}'")
                            
            except Exception as e:
                print(f"⚠ Migration error on {table_name}: {e}")

    if not db.is_closed():
        db.close()
