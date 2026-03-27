import sqlite3
import os
import bcrypt
import sys

def reset_database(create_default_admin=True):
    """
    Reset the database with proper schema and optional default admin
    """
    db_file = 'church.db'
    
    # 1. Remove old database
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
            print("✅ Old database removed.")
        except PermissionError:
            print("❌ Error: Could not delete 'church.db'. Close any program using it and try again.")
            return False
        except Exception as e:
            print(f"❌ Error: {e}")
            return False

    # 2. Connect and create new tables
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        
        print("\n" + "="*50)
        print("🔨 CREATING DATABASE TABLES")
        print("="*50)
        
        # --- TABLE 1: USERS (with password hashing support) ---
        cursor.execute('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        ''')
        print("✅ Users table created")
        
        # --- TABLE 2: SATURDAY RECORDS (Attendance & Finance) ---
        cursor.execute('''
            CREATE TABLE saturday_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                saturday_no INTEGER,
                men INTEGER DEFAULT 0, 
                women INTEGER DEFAULT 0, 
                youth INTEGER DEFAULT 0, 
                children INTEGER DEFAULT 0,
                sunday INTEGER DEFAULT 0,
                tithe REAL DEFAULT 0.0, 
                offering REAL DEFAULT 0.0, 
                fr_amount REAL DEFAULT 0.0, 
                fr_type TEXT DEFAULT 'none',
                reg_giving REAL DEFAULT 0.0,
                year INTEGER DEFAULT 2026,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✅ Saturday records table created")
        
        # --- TABLE 3: SETTINGS (Global Targets) ---
        cursor.execute('''
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                target_attendance INTEGER DEFAULT 0,
                target_offering REAL DEFAULT 0.0,
                target_men INTEGER DEFAULT 1300,
                target_women INTEGER DEFAULT 1600,
                target_youth INTEGER DEFAULT 780,
                target_children INTEGER DEFAULT 900,
                target_tithe INTEGER DEFAULT 800000,
                target_offering_amount INTEGER DEFAULT 200000,
                church_name TEXT DEFAULT 'My Church',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✅ Settings table created")
        
        # Insert default settings
        cursor.execute('''
            INSERT OR IGNORE INTO settings 
            (id, target_attendance, target_offering, target_men, target_women, 
             target_youth, target_children, target_tithe, target_offering_amount)
            VALUES (1, 0, 0.0, 1300, 1600, 780, 900, 800000, 200000)
        ''')
        
        # --- TABLE 4: CHURCH PROJECTS ---
        cursor.execute('''
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'ongoing',
                target_amount REAL DEFAULT 0.0,
                raised_amount REAL DEFAULT 0.0,
                start_date DATE,
                end_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✅ Projects table created")

        # --- TABLE 5: EXPENSES ---
        cursor.execute('''
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                description TEXT,
                expense_date DATE DEFAULT CURRENT_DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✅ Expenses table created")
        
        # --- TABLE 6: ACTIVITY LOGS (for admin tracking) ---
        cursor.execute('''
            CREATE TABLE activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                details TEXT,
                ip_address TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        print("✅ Activity logs table created")
        
        # --- TABLE 7: AUDIT TRAIL (for data changes) ---
        cursor.execute('''
            CREATE TABLE audit_trail (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                record_id INTEGER,
                action TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                user_id INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✅ Audit trail table created")
        
        # --- CREATE DEFAULT ADMIN USER (if requested) ---
        if create_default_admin:
            try:
                # Default admin password: Admin@2026!
                default_password = "Admin@2026!"
                salt = bcrypt.gensalt(rounds=12)
                hashed = bcrypt.hashpw(default_password.encode('utf-8'), salt)
                
                cursor.execute('''
                    INSERT INTO users (username, password, role) 
                    VALUES (?, ?, ?)
                ''', ('admin', hashed.decode('utf-8'), 'admin'))
                
                print("\n" + "="*50)
                print("👤 DEFAULT ADMIN USER CREATED")
                print("="*50)
                print("📝 Username: admin")
                print("🔑 Password: Admin@2026!")
                print("⚠️  Please change this password after first login!")
                print("="*50)
                
            except Exception as e:
                print(f"⚠️ Could not create default admin: {e}")
        
        # --- CREATE TEST DATA (optional - comment out if not needed) ---
        create_test_data = False  # Set to True if you want sample data
        if create_test_data:
            try:
                # Insert sample Saturday records
                sample_data = [
                    (1, 450, 550, 300, 200, 1500, 500, 50, 'planned', 2026),
                    (2, 480, 520, 320, 210, 1800, 600, 100, 'emergency', 2026),
                    (3, 500, 600, 350, 250, 2000, 700, 0, 'none', 2026),
                ]
                
                for sat in sample_data:
                    giving = sat[7] + sat[8]  # tithe + offering
                    cursor.execute('''
                        INSERT INTO saturday_records 
                        (saturday_no, men, women, youth, children, tithe, offering, fr_amount, fr_type, year, reg_giving)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (sat[0], sat[1], sat[2], sat[3], sat[4], sat[5], sat[6], sat[7], sat[8], sat[9], giving))
                
                print("✅ Sample Saturday records created")
                
                # Insert sample projects
                projects = [
                    ('New Sanctuary', 'Building a new sanctuary for the growing congregation', 'ongoing', 500000, 120000),
                    ('Youth Center', 'Renovating youth meeting space', 'planning', 100000, 0),
                ]
                
                for proj in projects:
                    cursor.execute('''
                        INSERT INTO projects (name, description, status, target_amount, raised_amount)
                        VALUES (?, ?, ?, ?, ?)
                    ''', proj)
                
                print("✅ Sample projects created")
                
            except Exception as e:
                print(f"⚠️ Could not create test data: {e}")
        
        # Commit all changes
        conn.commit()
        
        print("\n" + "="*50)
        print("🎉 DATABASE RESET COMPLETE!")
        print("="*50)
        print("📊 Tables created:")
        print("   - users")
        print("   - saturday_records")
        print("   - settings")
        print("   - projects")
        print("   - expenses")
        print("   - activity_logs")
        print("   - audit_trail")
        print("="*50)
        
        conn.close()
        return True
        
    except sqlite3.Error as e:
        print(f"❌ SQLite error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def verify_database():
    """Verify that the database was created correctly"""
    try:
        conn = sqlite3.connect('church.db')
        cursor = conn.cursor()
        
        # Get list of tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        print("\n" + "="*50)
        print("🔍 DATABASE VERIFICATION")
        print("="*50)
        print("Tables found:")
        for table in tables:
            print(f"   - {table[0]}")
            
            # Get row count for each table
            cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
            count = cursor.fetchone()[0]
            print(f"     Records: {count}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f" Verification failed: {e}")
        return False

def main():
    """Main function with command-line options"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Reset church database')
    parser.add_argument('--no-admin', action='store_true', 
                       help='Do not create default admin user')
    parser.add_argument('--test-data', action='store_true',
                       help='Create sample test data')
    parser.add_argument('--verify', action='store_true',
                       help='Verify database after creation')
    
    args = parser.parse_args()
    
    print("\n" + "="*60)
    print("🏛️  CHURCH MANAGEMENT SYSTEM - DATABASE RESET")
    print("="*60)
    
    # Confirm reset
    if os.path.exists('church.db'):
        response = input("⚠️  This will DELETE all existing data. Continue? (y/N): ")
        if response.lower() != 'y':
            print(" Operation cancelled.")
            return
    
    # Reset database
    success = reset_database(create_default_admin=not args.no_admin)
    
    if success and args.verify:
        verify_database()
    
    
    if success:
        print("\n" + "="*60)
        print(" DATABASE READY!")
        print("="*60)
        print(" You can now run: python app.py")
        print("="*60)

if __name__ == '__main__':
    main()