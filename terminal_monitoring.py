import subprocess
import sqlite3
from datetime import datetime
import time
import logging
from logging.handlers import RotatingFileHandler
import os

# Base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# File paths
DB_PATH = os.path.join(BASE_DIR, "sessions.db")
LOG_FILE = os.path.join(BASE_DIR, "script.log")

# List of servers to monitor
SERVERS = ["server1", "server2", "server3"]  # Replace with your server names
CHECK_INTERVAL = 60  # Check interval in seconds

# Create directory if it doesn't exist
os.makedirs(BASE_DIR, exist_ok=True)

# Logging configuration
handler = RotatingFileHandler(LOG_FILE, maxBytes=500 * 1024, backupCount=1, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    handlers=[handler]
)

def initialize_database():
    """Initialize the database."""
    logging.info("Initializing database...")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            # Create table for users and their total session time
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Users (
                    Username TEXT PRIMARY KEY,
                    TotalMinutes INTEGER NOT NULL
                );
            ''')
            # Create table for active sessions
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ActiveSessions (
                    SessionId TEXT PRIMARY KEY,
                    Username TEXT NOT NULL,
                    LogonTime TEXT NOT NULL
                );
            ''')
            conn.commit()
        logging.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logging.error(f"Error initializing database: {e}")

def get_active_sessions(server):
    """Get active sessions from the server."""
    logging.info(f"Checking server {server}...")
    try:
        result = subprocess.run(
            ['quser', f'/server:{server}'],
            capture_output=True,
            timeout=30,
            encoding='ibm866'
        )
        if result.returncode != 0:
            if "No users exist for *" in result.stderr:
                logging.info(f"No active users on server {server}.")
            else:
                logging.error(f"Error fetching sessions from server {server}. Error: {result.stderr}")
            return []

        output = result.stdout.strip()
        if "No users exist for *" in output:
            logging.info(f"No active users on server {server}.")
            return []

        sessions = []
        lines = output.splitlines()[1:]  # Skip the header
        for line in lines:
            parts = line.split()
            if len(parts) >= 5:
                username = parts[0]
                session_id = parts[2]
                state = parts[3]
                logon_time = f"{parts[-2]} {parts[-1]}"

                # Ignore system sessions (SessionId is not a number)
                if not session_id.isdigit():
                    logging.warning(f"Ignoring system session: User={username}, SessionId={session_id}.")
                    continue

                # Ignore specific user (e.g., admin)
                if username == "admin":
                    logging.info(f"Ignoring session for user admin (SessionId={session_id}).")
                    continue

                sessions.append({
                    "Username": username,
                    "SessionId": session_id,
                    "State": state,
                    "LogonTime": logon_time
                })
        logging.info(f"Found {len(sessions)} active sessions on server {server}.")
        return sessions
    except subprocess.TimeoutExpired:
        logging.error(f"Timeout while fetching sessions from server {server}.")
        return []
    except Exception as e:
        logging.error(f"Error fetching sessions from server {server}: {e}")
        return []

def update_user_time(username, session_duration):
    """Update user's total session time in the database."""
    # Ignore specific user (e.g., admin)
    if username == "admin":
        logging.info(f"Ignoring update for user admin.")
        return

    logging.info(f"Updating data for user {username} with {session_duration} minutes.")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO Users (Username, TotalMinutes)
                VALUES (?, COALESCE((SELECT TotalMinutes FROM Users WHERE Username = ?), 0) + ?);
            ''', (username, username, session_duration))
            conn.commit()
        logging.info(f"Data for user {username} updated successfully.")
    except sqlite3.Error as e:
        logging.error(f"Error updating data for user {username}: {e}")

def check_completed_sessions(active_sessions):
    """Check for completed sessions and update the database."""
    current_time = datetime.now()
    completed_sessions = []

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()

            # Get all active sessions from the database
            cursor.execute("SELECT SessionId, Username, LogonTime FROM ActiveSessions;")
            db_sessions = cursor.fetchall()

            # Collect all active session IDs from the current check
            active_session_ids = {session["SessionId"] for session in active_sessions}

            # Check for completed sessions
            for session_id, username, logon_time_str in db_sessions:
                if session_id not in active_session_ids:
                    # Session is completed
                    logon_time = datetime.strptime(logon_time_str, "%d.%m.%Y %H:%M")
                    session_duration = round((current_time - logon_time).total_seconds() / 60)
                    completed_sessions.append((username, session_duration))

                    # Remove completed session from ActiveSessions
                    cursor.execute("DELETE FROM ActiveSessions WHERE SessionId = ?;", (session_id,))
                    logging.info(f"Session completed: User={username}, SessionId={session_id}, Duration={session_duration} min.")

            # Update user time in the database for completed sessions
            if completed_sessions:
                logging.info(f"Updating data for {len(completed_sessions)} completed sessions.")
                for username, duration in completed_sessions:
                    update_user_time(username, duration)
            else:
                logging.info("No completed sessions found.")

            # Add new active sessions to the database
            for session in active_sessions:
                session_id = session["SessionId"]
                username = session["Username"]
                logon_time = session["LogonTime"]

                # Check if the session already exists in the database
                cursor.execute("SELECT 1 FROM ActiveSessions WHERE SessionId = ?;", (session_id,))
                if not cursor.fetchone():
                    # Add new session
                    cursor.execute("INSERT INTO ActiveSessions (SessionId, Username, LogonTime) VALUES (?, ?, ?);",
                                  (session_id, username, logon_time))
                    logging.info(f"New session added to the database: User={username}, SessionId={session_id}, LogonTime={logon_time}.")

            conn.commit()
    except sqlite3.Error as e:
        logging.error(f"Error working with the database: {e}")

def main():
    """Main function."""
    initialize_database()
    last_report_time = datetime.now()  # Time of the last report generation

    try:
        while True:
            logging.info("Starting a new check cycle.")
            all_active_sessions = []

            # Get active sessions from all servers
            for server in SERVERS:
                sessions = get_active_sessions(server)
                all_active_sessions.extend(sessions)

            # Check for completed sessions and update the database
            check_completed_sessions(all_active_sessions)

            # Example: Generate a report or export data (optional)
            # You can add your own logic here to export data to files, databases, or external APIs.
            # For example:
            # - Export data to a CSV file.
            # - Send data to an external API.
            # - Generate a PDF report.

            # Example: Export data to a CSV file
            # with open("report.csv", "w") as file:
            #     file.write("Username,TotalMinutes\n")
            #     with sqlite3.connect(DB_PATH) as conn:
            #         cursor = conn.cursor()
            #         cursor.execute("SELECT Username, TotalMinutes FROM Users;")
            #         for row in cursor.fetchall():
            #             file.write(f"{row[0]},{row[1]}\n")

            # Wait before the next cycle
            logging.info(f"Waiting {CHECK_INTERVAL} seconds before the next check...")
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logging.info("Script stopped by the user.")
    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        logging.info("Script execution completed.")

if __name__ == "__main__":
    main()