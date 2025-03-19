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
from flask import Flask, jsonify
import threading
import sys
import dropbox
import re
import openai
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import httpx

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
        required_vars = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_SIGNING_SECRET", "OPENAI_API_KEY", "SPREADSHEET_ID", "DROPBOX_ACCESS_TOKEN"]
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
openai.api_key = os.getenv("OPENAI_API_KEY")

# Initialize Slack app with signing secret
logger.info("Initializing Slack app...")
app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"]
)
logger.info("Slack app initialized successfully")

# Get bot's own ID and user ID
bot_info = app.client.auth_test()
BOT_USER_ID = bot_info["user_id"]  # This is the ID we need for mentions
logger.info(f"Bot User ID: {BOT_USER_ID}")

# Initialize Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
sheet_service = None

# Initialize Dropbox client
dbx = dropbox.Dropbox(os.environ.get("DROPBOX_ACCESS_TOKEN"))

# Initialize conversation history
conversation_history = {}

def get_google_sheets_service():
    """Initialize and return Google Sheets service."""
    try:
        # For Railway, we'll use the service account JSON directly from environment
        service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
        if not service_account_json:
            logger.error("GOOGLE_SERVICE_ACCOUNT environment variable is not set")
            return None
            
        try:
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

def get_dropbox_shared_link(path):
    """Get a shared link for a Dropbox file."""
    try:
        # First check if a shared link already exists
        try:
            shared_links = dbx.sharing_list_shared_links(path=path)
            if shared_links.links:
                # If a link exists, return it
                return shared_links.links[0].url
        except Exception as e:
            logger.error(f"Error checking existing shared links: {e}")
        
        # If no link exists, create a new one with proper settings
        shared_link = dbx.sharing_create_shared_link_with_settings(
            path=path,
            settings=dropbox.sharing.SharedLinkSettings(
                requested_visibility=dropbox.sharing.RequestedVisibility.public,
                expires=None,
                link_password=None,
                audience=dropbox.sharing.LinkAudience.public,
                access=dropbox.sharing.RequestedLinkAccessLevel.viewer
            )
        )
        
        # Convert the URL to a direct download link
        url = shared_link.url
        url = url.replace('www.dropbox.com', 'dl.dropboxusercontent.com')
        url = url.replace('?dl=0', '?dl=1')
        
        return url
    except Exception as e:
        logger.error(f"Error creating shared link: {e}")
        return None

def search_dropbox(query):
    """Search for files in Dropbox."""
    try:
        # First try to get recent files
        recent_files = []
        try:
            recent = dbx.files_list_folder_get_latest_cursor(path="")
            while recent.has_more:
                recent_files.extend(recent.entries)
                recent = dbx.files_list_folder_continue(cursor=recent.cursor)
        except Exception as e:
            logger.error(f"Error getting recent files: {e}")

        # Then perform the search
        results = dbx.files_search_v2(query)
        files = []
        
        # Process search results
        for match in results.matches:
            try:
                # Get shared link for each file
                shared_link = get_dropbox_shared_link(match.metadata.path_lower)
                if shared_link:  # Only add files that we successfully got a link for
                    files.append({
                        "name": match.metadata.name,
                        "path": match.metadata.path_lower,
                        "type": match.metadata.get(".tag", "file"),
                        "modified": match.metadata.server_modified,
                        "shared_link": shared_link,
                        "is_recent": any(rf.path_lower == match.metadata.path_lower for rf in recent_files)
                    })
                    logger.info(f"Successfully created shared link for {match.metadata.name}")
                else:
                    logger.warning(f"Could not create shared link for {match.metadata.name}")
            except Exception as e:
                logger.error(f"Error processing file {match.metadata.name}: {e}")
                continue

        # If no search results but we have recent files, check if any match the query
        if not files and recent_files:
            query_terms = query.lower().split()
            for file in recent_files:
                if any(term in file.name.lower() for term in query_terms):
                    try:
                        shared_link = get_dropbox_shared_link(file.path_lower)
                        if shared_link:
                            files.append({
                                "name": file.name,
                                "path": file.path_lower,
                                "type": file.get(".tag", "file"),
                                "modified": file.server_modified,
                                "shared_link": shared_link,
                                "is_recent": True
                            })
                    except Exception as e:
                        logger.error(f"Error processing recent file {file.name}: {e}")
                        continue

        return files
    except Exception as e:
        logger.error(f"Error searching Dropbox: {e}")
        return []

def list_dropbox_folders(limit=20):
    """List folders in Dropbox."""
    try:
        folders = []
        result = dbx.files_list_folder(path="")
        
        while result.entries and len(folders) < limit:
            for entry in result.entries:
                if entry.get(".tag") == "folder":
                    folders.append({
                        "name": entry.name,
                        "path": entry.path_lower,
                        "modified": entry.server_modified
                    })
                    if len(folders) >= limit:
                        break
            
            if result.has_more and len(folders) < limit:
                result = dbx.files_list_folder_continue(cursor=result.cursor)
            else:
                break
                
        return folders
    except Exception as e:
        logger.error(f"Error listing Dropbox folders: {e}")
        return []

def get_ai_response(prompt, context=None):
    """Get response from OpenAI API with context."""
    try:
        messages = [
            {"role": "system", "content": """You are Gilbert AI, a helpful and friendly assistant for a creative agency. 
            You help with client communication, project management, and creative tasks. 
            You have a conversational tone and remember important information from conversations.
            You have access to:
            - Client information and project statuses from the database
            - Files and documents from Dropbox
            If you don't know something, say so and offer to help find the answer.
            When discussing clients or projects, provide relevant context from the available information.
            If someone asks about a client or project that isn't in the database yet, explain that you don't have information about it yet and offer to help add it to the database.
            When sharing Dropbox links, ALWAYS include the actual file links in your response. Format them like this:
            - [File Name](file_link) (modified: date)
            If you find files in Dropbox, you MUST share the links and explain what each file is.
            When listing folders, show the folder names and their last modified dates."""}
        ]
        
        if context:
            messages.append({"role": "system", "content": f"Context from previous conversations: {context}"})
        
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
        
        # Check for Dropbox-related requests
        dropbox_related = any(word in prompt.lower() for word in [
            "file", "document", "link", "folder", "list", "show", "dropbox", 
            "directory", "folder", "folders", "files", "documents"
        ])
        
        if dropbox_related:
            # If specifically asking for folders
            if any(word in prompt.lower() for word in ["folder", "folders", "list", "show"]):
                folders = list_dropbox_folders()
                if folders:
                    folder_info = []
                    for folder in folders:
                        modified_date = datetime.fromtimestamp(folder["modified"]).strftime("%Y-%m-%d %H:%M")
                        folder_info.append(f"- {folder['name']} (modified: {modified_date})")
                    
                    messages.append({
                        "role": "system",
                        "content": f"Found these folders in Dropbox:\n" + "\n".join(folder_info)
                    })
            
            # If asking for files
            else:
                # Extract potential file name or type
                file_query = re.sub(r'[^\w\s]', '', prompt.lower())
                dropbox_results = search_dropbox(file_query)
                if dropbox_results:
                    # Format the file information for the AI
                    file_info = []
                    for file in dropbox_results:
                        modified_date = datetime.fromtimestamp(file["modified"]).strftime("%Y-%m-%d %H:%M")
                        file_info.append(f"- [{file['name']}]({file['shared_link']}) (modified: {modified_date})")
                        if file.get("is_recent"):
                            file_info[-1] += " [Recent]"
                    
                    messages.append({
                        "role": "system", 
                        "content": f"Found these files in Dropbox:\n" + "\n".join(file_info)
                    })
        
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
        
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error getting AI response: {e}")
        return "I apologize, but I'm having trouble processing that request right now."

def extract_important_info(text):
    """Extract important information from text using AI."""
    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Extract important information from the text that should be remembered. Focus on facts, decisions, deadlines, and key details. Return only the important information in a concise format."},
                {"role": "user", "content": text}
            ],
            temperature=0.7,
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
        spreadsheet = sheet_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
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
                sheet_service.spreadsheets().batchUpdate(
                    spreadsheetId=SPREADSHEET_ID,
                    body=body
                ).execute()
            
            # Update headers and sample data
            range_name = f"{sheet_name}!A1:{chr(65 + len(structure['headers']) - 1)}1"
            body = {
                'values': [structure['headers']]
            }
            sheet_service.spreadsheets().values().update(
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
            sheet_service.spreadsheets().values().update(
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
        # Debug logging
        logger.info(f"DEBUG: Received message event: {event}")
        logger.info(f"DEBUG: Message text: {event.get('text', '')}")
        logger.info(f"DEBUG: User ID: {event.get('user')}")
        logger.info(f"DEBUG: Channel ID: {event.get('channel')}")
        logger.info(f"DEBUG: Thread TS: {event.get('thread_ts', event.get('ts'))}")
        
        # Get message text and user info
        text = event.get("text", "")
        user_id = event.get("user")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts", event.get("ts"))
        
        logger.info(f"Processing message: text='{text}', user_id='{user_id}', channel_id='{channel_id}'")
        
        # Check if this is a mention or thread reply
        # Look for the bot's user ID in the message, preserving case
        is_mention = f"<@{BOT_USER_ID}>" in text
        is_thread_reply = event.get("thread_ts") is not None
        
        logger.info(f"Message type: mention={is_mention}, thread_reply={is_thread_reply}")
        logger.info(f"Looking for mention: <@{BOT_USER_ID}>")
        
        if not (is_mention or is_thread_reply):
            logger.info("Ignoring message - not a mention or thread reply")
            return
            
        # Get user info
        user_info = app.client.users_info(user=user_id)
        user_name = user_info["user"]["real_name"]
        logger.info(f"User info retrieved: {user_name}")
        
        # Test Dropbox connection if requested
        if "test dropbox" in text.lower():
            logger.info("Testing Dropbox connection...")
            test_result = test_dropbox_connection()
            if test_result["status"] == "success":
                used_gb = round(test_result["used_space"] / (1024**3), 2)
                total_gb = round(test_result["total_space"] / (1024**3), 2)
                response = f"✅ Dropbox connection successful!\nAccount: {test_result['account_name']}\nEmail: {test_result['email']}\nStorage: {used_gb}GB used of {total_gb}GB"
            else:
                response = f"❌ Dropbox connection failed: {test_result['message']}"
            logger.info(f"Sending response: {response}")
            app.client.chat_postMessage(
                channel=channel_id,
                text=response,
                thread_ts=thread_ts
            )
            return
            
        # Get conversation history
        history_key = f"{user_id}_{channel_id}"
        history = conversation_history.get(history_key, [])
        logger.info(f"Retrieved conversation history for {history_key}")
        
        # Get AI response
        logger.info("Getting AI response...")
        response = get_ai_response(text, history)
        logger.info(f"AI response received: {response[:100]}...")  # Log first 100 chars
        
        # Update conversation history
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": response})
        if len(history) > 10:  # Keep last 5 exchanges (10 messages)
            history = history[-10:]
        conversation_history[history_key] = history
        logger.info("Updated conversation history")
        
        # Send response
        logger.info("Sending response to Slack...")
        app.client.chat_postMessage(
            channel=channel_id,
            text=response,
            thread_ts=thread_ts
        )
        logger.info("Response sent successfully")
        
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)
        try:
            app.client.chat_postMessage(
                channel=channel_id,
                text="I apologize, but I encountered an error processing your request. Please try again later.",
                thread_ts=thread_ts
            )
        except Exception as send_error:
            logger.error(f"Error sending error message: {send_error}", exc_info=True)

# Initialize the handler for Socket Mode
if __name__ == "__main__":
    try:
        # Log startup information
        logger.info("Starting application...")
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Environment variables: {list(os.environ.keys())}")
        
        # Initialize Google Sheets service and set up sheets
        sheet_service = get_google_sheets_service()
        if sheet_service:
            setup_sheets()
        
        # Start the Slack bot in a separate thread
        def run_slack_bot():
            try:
                logger.info("Starting Slack bot...")
                handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
                logger.info("Socket Mode handler created")
                handler.start()
            except Exception as e:
                logger.error(f"Slack bot error: {e}", exc_info=True)
                sys.exit(1)

        # Start Slack bot in a separate thread
        slack_thread = threading.Thread(target=run_slack_bot, daemon=True)
        slack_thread.start()
        logger.info("Slack bot thread started")
        
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
        logger.error(f"Application error: {e}", exc_info=True)
        sys.exit(1) 