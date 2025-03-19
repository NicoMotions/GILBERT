import os
import logging
import json
import time
from datetime import datetime
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from openai import OpenAI
from flask import Flask
import threading
import sys
import asana
import dropbox
import re

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app for health check
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    """Health check endpoint."""
    try:
        # Check if required environment variables are present
        required_vars = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_SIGNING_SECRET", "OPENAI_API_KEY", "SPREADSHEET_ID", "ASANA_ACCESS_TOKEN", "ASANA_WORKSPACE_ID", "DROPBOX_ACCESS_TOKEN"]
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        
        if missing_vars:
            logger.error(f"Missing required environment variables: {missing_vars}")
            return {'status': 'error', 'missing_vars': missing_vars}, 500
            
        # Test Google Sheets connection
        service = get_google_sheets_service()
        if not service:
            logger.error("Failed to connect to Google Sheets")
            return {'status': 'error', 'message': 'Google Sheets connection failed'}, 500
            
        return {'status': 'healthy', 'timestamp': datetime.now().isoformat()}, 200
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return {'status': 'error', 'message': str(e)}, 500

def run_flask():
    """Run Flask server."""
    try:
        port = int(os.environ.get('PORT', 8080))
        logger.info(f"Starting Flask server on port {port}")
        flask_app.run(
            host='0.0.0.0',
            port=port,
            debug=False,
            use_reloader=False
        )
    except Exception as e:
        logger.error(f"Flask server error: {e}")
        sys.exit(1)

# Initialize OpenAI client
openai = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.openai.com/v1"
)

# Initialize Slack app with signing secret
app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"]
)

# Initialize Asana client
asana_client = asana.ApiClient().access_token(os.environ.get("ASANA_ACCESS_TOKEN"))
asana_workspace_id = os.environ.get("ASANA_WORKSPACE_ID")

# Initialize Dropbox client
dbx = dropbox.Dropbox(os.environ.get("DROPBOX_ACCESS_TOKEN"))

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")

def get_google_sheets_service():
    """Initialize and return Google Sheets service."""
    try:
        # For Railway, we'll use the service account JSON directly from environment
        service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
        if not service_account_json:
            logger.error("GOOGLE_SERVICE_ACCOUNT environment variable is not set")
            return None
            
        try:
            import json
            service_account_info = json.loads(service_account_json)
            creds = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=SCOPES
            )
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing service account JSON: {e}")
            return None
            
        service = build('sheets', 'v4', credentials=creds)
        
        # Test the connection
        try:
            service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
            logger.info("Successfully connected to Google Sheets")
            return service
        except HttpError as e:
            logger.error(f"Error testing Google Sheets connection: {e}")
            return None
            
    except Exception as e:
        logger.error(f"Error initializing Google Sheets service: {e}")
        return None

def append_to_sheet(sheet_name, values):
    """Append data to specified Google Sheet."""
    if not SPREADSHEET_ID:
        logger.error("No spreadsheet ID configured")
        return False
        
    try:
        service = get_google_sheets_service()
        if not service:
            logger.error("Could not initialize Google Sheets service")
            return False
        
        body = {
            'values': values
        }
        result = service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f'{sheet_name}!A:Z',
            valueInputOption='RAW',
            body=body
        ).execute()
        logger.info(f"Successfully appended data to sheet: {sheet_name}")
        return True
    except HttpError as e:
        logger.error(f"Error appending to sheet: {e}")
        return False

def read_from_sheet(sheet_name, range_name):
    """Read data from specified Google Sheet."""
    if not SPREADSHEET_ID:
        logger.error("No spreadsheet ID configured")
        return None
        
    try:
        service = get_google_sheets_service()
        if not service:
            logger.error("Could not initialize Google Sheets service")
            return None
        
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f'{sheet_name}!{range_name}'
        ).execute()
        values = result.get('values', [])
        logger.info(f"Successfully read {len(values)} rows from sheet: {sheet_name}")
        return values
    except HttpError as e:
        logger.error(f"Error reading from sheet: {e}")
        return None

def get_client_info(client_name):
    """Get information about a specific client."""
    try:
        data = read_from_sheet("Clients", "A:E")
        if not data:
            return None
            
        for row in data:
            if row[0].lower() == client_name.lower():
                return {
                    "name": row[0],
                    "contact": row[1],
                    "projects": row[2].split(","),
                    "key_dates": row[3],
                    "notes": row[4]
                }
        return None
    except Exception as e:
        logger.error(f"Error getting client info: {e}")
        return None

def get_project_status(project_name):
    """Get status of a specific project."""
    try:
        data = read_from_sheet("Projects", "A:F")
        if not data:
            return None
            
        for row in data:
            if row[0].lower() == project_name.lower():
                return {
                    "name": row[0],
                    "client": row[1],
                    "status": row[2],
                    "due_date": row[3],
                    "team": row[4].split(","),
                    "notes": row[5]
                }
        return None
    except Exception as e:
        logger.error(f"Error getting project status: {e}")
        return None

def get_asana_projects():
    """Get all active projects from Asana."""
    try:
        projects = asana_client.projects.get_projects_for_workspace(asana_workspace_id)
        return [
            {
                "name": project["name"],
                "status": project.get("current_status", {}).get("status", "Unknown"),
                "due_date": project.get("due_date"),
                "team": [member["name"] for member in project.get("team", [])],
                "tasks": get_project_tasks(project["gid"])
            }
            for project in projects
        ]
    except Exception as e:
        logger.error(f"Error getting Asana projects: {e}")
        return []

def get_project_tasks(project_gid):
    """Get tasks for a specific Asana project."""
    try:
        tasks = asana_client.tasks.get_tasks_for_project(project_gid)
        return [
            {
                "name": task["name"],
                "status": task.get("completed", False),
                "assignee": task.get("assignee", {}).get("name"),
                "due_date": task.get("due_date")
            }
            for task in tasks
        ]
    except Exception as e:
        logger.error(f"Error getting project tasks: {e}")
        return []

def search_dropbox(query):
    """Search for files in Dropbox."""
    try:
        results = dbx.files_search_v2(query)
        return [
            {
                "name": match.metadata.name,
                "path": match.metadata.path_lower,
                "type": match.metadata.get(".tag", "file"),
                "modified": match.metadata.server_modified
            }
            for match in results.matches
        ]
    except Exception as e:
        logger.error(f"Error searching Dropbox: {e}")
        return []

def get_dropbox_shared_link(path):
    """Get a shared link for a Dropbox file."""
    try:
        shared_link = dbx.sharing_create_shared_link(path)
        return shared_link.url
    except Exception as e:
        logger.error(f"Error creating shared link: {e}")
        return None

def get_ai_response(prompt, context=None):
    """Get response from OpenAI API with context."""
    try:
        messages = [
            {"role": "system", "content": """You are Gilbert AI, a helpful and friendly assistant for a creative agency. 
            You help with client communication, project management, and creative tasks. 
            You have a conversational tone and remember important information from conversations.
            You have access to:
            - Client information and project statuses from the database
            - Active projects and tasks from Asana
            - Files and documents from Dropbox
            If you don't know something, say so and offer to help find the answer.
            When discussing clients or projects, provide relevant context from the available information.
            If someone asks about a client or project that isn't in the database yet, explain that you don't have information about it yet and offer to help add it to the database.
            When sharing Dropbox links, explain what the file is and why it might be relevant."""}
        ]
        
        if context:
            messages.append({"role": "system", "content": f"Context from previous conversations: {context}"})
        
        # Get Asana projects
        asana_projects = get_asana_projects()
        if asana_projects:
            messages.append({"role": "system", "content": f"Current Asana projects: {json.dumps(asana_projects, indent=2)}"})
        
        # Check if the prompt is about a client or project
        client_info = None
        project_info = None
        unknown_client = None
        unknown_project = None
        
        # Look for client names in the prompt
        client_data = read_from_sheet("Clients", "A:A")
        if client_data:
            for row in client_data:
                if row[0].lower() in prompt.lower():
                    client_info = get_client_info(row[0])
                    break
            # If no client found but prompt seems to be about a client
            if not client_info and any(word in prompt.lower() for word in ["client", "company", "business"]):
                # Try to extract potential client name from the prompt
                words = prompt.lower().split()
                for i, word in enumerate(words):
                    if word in ["client", "company", "business"] and i > 0:
                        unknown_client = words[i-1]
                        break
        
        # Look for project names in the prompt
        project_data = read_from_sheet("Projects", "A:A")
        if project_data:
            for row in project_data:
                if row[0].lower() in prompt.lower():
                    project_info = get_project_status(row[0])
                    break
            # If no project found but prompt seems to be about a project
            if not project_info and any(word in prompt.lower() for word in ["project", "campaign", "work"]):
                # Try to extract potential project name from the prompt
                words = prompt.lower().split()
                for i, word in enumerate(words):
                    if word in ["project", "campaign", "work"] and i > 0:
                        unknown_project = words[i-1]
                        break
        
        # Check for Dropbox file requests
        if "file" in prompt.lower() or "document" in prompt.lower():
            # Extract potential file name or type
            file_query = re.sub(r'[^\w\s]', '', prompt.lower())
            dropbox_results = search_dropbox(file_query)
            if dropbox_results:
                messages.append({"role": "system", "content": f"Relevant Dropbox files: {json.dumps(dropbox_results, indent=2)}"})
        
        # Add relevant context to the prompt
        if client_info:
            messages.append({"role": "system", "content": f"Client information: {client_info}"})
        if project_info:
            messages.append({"role": "system", "content": f"Project information: {project_info}"})
        
        # Add information about unknown clients/projects
        if unknown_client:
            messages.append({"role": "system", "content": f"Note: The client '{unknown_client}' is not in the database yet."})
        if unknown_project:
            messages.append({"role": "system", "content": f"Note: The project '{unknown_project}' is not in the database yet."})
        
        messages.append({"role": "user", "content": prompt})
        
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=300
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error getting AI response: {e}")
        return "I apologize, but I'm having trouble processing that request right now."

def extract_important_info(text):
    """Extract important information from text using AI."""
    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Extract important information from the text that should be remembered. Focus on facts, decisions, deadlines, and key details. Return only the important information in a concise format."},
                {"role": "user", "content": text}
            ],
            max_tokens=150
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error extracting important info: {e}")
        return None

def setup_sheets():
    """Set up the required sheets structure in the Google Spreadsheet."""
    try:
        # Define the sheets structure
        sheets_structure = {
            "Clients": {
                "headers": ["Client Name", "Contact Information", "Projects", "Key Dates", "Notes"],
                "sample_data": [
                    ["Example Client", "contact@example.com", "Project A, Project B", "Contract Start: 2024-01-01", "Key client notes"]
                ]
            },
            "Projects": {
                "headers": ["Project Name", "Client", "Status", "Due Date", "Team Members", "Notes"],
                "sample_data": [
                    ["Project A", "Example Client", "In Progress", "2024-06-01", "John, Jane", "Project notes"]
                ]
            }
        }
        
        # Get existing sheets
        spreadsheet = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        existing_sheets = {sheet['properties']['title'] for sheet in spreadsheet['sheets']}
        
        # Create or update each sheet
        for sheet_name, structure in sheets_structure.items():
            if sheet_name not in existing_sheets:
                # Create new sheet
                body = {
                    'requests': [{
                        'addSheet': {
                            'properties': {
                                'title': sheet_name
                            }
                        }
                    }]
                }
                service.spreadsheets().batchUpdate(
                    spreadsheetId=SPREADSHEET_ID,
                    body=body
                ).execute()
            
            # Update headers and sample data
            range_name = f"{sheet_name}!A1:{chr(65 + len(structure['headers']) - 1)}1"
            body = {
                'values': [structure['headers']]
            }
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=range_name,
                valueInputOption='RAW',
                body=body
            ).execute()
            
            # Add sample data if sheet is empty
            range_name = f"{sheet_name}!A2:{chr(65 + len(structure['headers']) - 1)}2"
            body = {
                'values': structure['sample_data']
            }
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=range_name,
                valueInputOption='RAW',
                body=body
            ).execute()
        
        logger.info("Successfully set up sheets structure")
        return True
    except Exception as e:
        logger.error(f"Error setting up sheets: {e}")
        return False

def test_asana_connection():
    """Test the Asana connection and return basic workspace info."""
    try:
        # Get workspace info
        workspace = asana_client.workspaces.get_workspace(asana_workspace_id)
        
        # Get projects count
        projects = asana_client.projects.get_projects_for_workspace(asana_workspace_id)
        projects_count = len(list(projects))
        
        return {
            "workspace_name": workspace.name,
            "projects_count": projects_count,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error testing Asana connection: {e}")
        return {
            "status": "error",
            "message": str(e)
        }

def test_dropbox_connection():
    """Test the Dropbox connection and return basic account info."""
    try:
        # Get account info
        account = dbx.users_get_current_account()
        
        # Get space usage
        space_usage = dbx.users_get_space_usage()
        
        return {
            "account_name": account.name.display_name,
            "email": account.email,
            "used_space": space_usage.used,
            "total_space": space_usage.allocation.get_individual().allocated,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error testing Dropbox connection: {e}")
        return {
            "status": "error",
            "message": str(e)
        }

# Slack event handlers
@app.event("message")
def handle_message(event):
    """Handle incoming messages."""
    try:
        # Get message text and user info
        text = event.get("text", "")
        user_id = event.get("user")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts", event.get("ts"))
        
        # Check if this is a mention or thread reply
        is_mention = any(mention in text.lower() for mention in ["<@gilbert ai>", "<@gilbertai>"])
        is_thread_reply = event.get("thread_ts") is not None
        
        if not (is_mention or is_thread_reply):
            return
            
        # Get user info
        user_info = app.client.users_info(user=user_id)
        user_name = user_info["user"]["real_name"]
        
        # Test Asana connection if requested
        if "test asana" in text.lower():
            test_result = test_asana_connection()
            if test_result["status"] == "success":
                response = f"✅ Asana connection successful!\nWorkspace: {test_result['workspace_name']}\nActive Projects: {test_result['projects_count']}"
            else:
                response = f"❌ Asana connection failed: {test_result['message']}"
            app.client.chat_postMessage(
                channel=channel_id,
                text=response,
                thread_ts=thread_ts
            )
            return
            
        # Test Dropbox connection if requested
        if "test dropbox" in text.lower():
            test_result = test_dropbox_connection()
            if test_result["status"] == "success":
                used_gb = round(test_result["used_space"] / (1024**3), 2)
                total_gb = round(test_result["total_space"] / (1024**3), 2)
                response = f"✅ Dropbox connection successful!\nAccount: {test_result['account_name']}\nEmail: {test_result['email']}\nStorage: {used_gb}GB used of {total_gb}GB"
            else:
                response = f"❌ Dropbox connection failed: {test_result['message']}"
            app.client.chat_postMessage(
                channel=channel_id,
                text=response,
                thread_ts=thread_ts
            )
            return
        
        # Get thread context if this is a reply
        thread_context = ""
        if is_thread_reply:
            thread_messages = app.client.conversations_replies(channel=channel_id, ts=thread_ts)
            if thread_messages["messages"]:
                # Get the original message and Gilbert's response
                original_message = thread_messages["messages"][0]["text"]
                gilbert_response = None
                for msg in thread_messages["messages"][1:]:
                    if msg.get("bot_id") or msg.get("subtype") == "bot_message":
                        gilbert_response = msg["text"]
                        break
                
                if gilbert_response:
                    thread_context = f"Original message: {original_message}\nGilbert's response: {gilbert_response}"
        
        # Get context from previous conversations
        context = ""
        context_data = read_from_sheet("Memory", "A:C")
        if context_data:
            # Get last 5 relevant memories
            relevant_memories = [row[2] for row in context_data[-5:]]
            context = " ".join(relevant_memories)
            logger.info(f"Context from previous conversations: {context}")
        
        # Get AI response
        response = get_ai_response(text, context)
        logger.info(f"AI response: {response}")
        
        # Extract and store important information
        important_info = extract_important_info(text)
        if important_info and SPREADSHEET_ID:  # Only try to store if we have a spreadsheet ID
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            values = [[timestamp, user_id, important_info]]
            append_to_sheet("Memory", values)
            logger.info(f"Stored important info: {important_info}")
        
        # Send response in thread if it's a thread reply, otherwise as a new message
        if thread_ts:
            app.client.chat_postMessage(
                channel=channel_id,
                text=response,
                thread_ts=thread_ts
            )
        else:
            app.client.chat_postMessage(
                channel=channel_id,
                text=response
            )
        logger.info("Response sent successfully")
    
    except Exception as e:
        logger.error(f"Error handling message: {e}")

# Initialize the handler for Socket Mode
if __name__ == "__main__":
    try:
        # Log startup information
        logger.info("Starting application...")
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Environment variables: {list(os.environ.keys())}")
        
        # Start the Slack bot in a separate thread
        def run_slack_bot():
            try:
                logger.info("Starting Slack bot...")
                handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
                handler.start()
            except Exception as e:
                logger.error(f"Slack bot error: {e}")
                sys.exit(1)

        # Start Slack bot in a separate thread
        slack_thread = threading.Thread(target=run_slack_bot, daemon=True)
        slack_thread.start()
        
        # Run Flask as the main process
        port = int(os.environ.get('PORT', 8080))
        logger.info(f"Starting Flask server on port {port}")
        flask_app.run(
            host='0.0.0.0',
            port=port,
            debug=False,
            use_reloader=False
        )
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1) 