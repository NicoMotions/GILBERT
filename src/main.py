import os
import logging
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
        required_vars = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_SIGNING_SECRET", "OPENAI_API_KEY", "SPREADSHEET_ID"]
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

def get_ai_response(prompt, context=None):
    """Get response from OpenAI API with context."""
    try:
        messages = [
            {"role": "system", "content": """You are Gilbert AI, a helpful and friendly assistant for a creative agency. 
            You help with client communication, project management, and creative tasks. 
            You have a conversational tone and remember important information from conversations.
            You have access to client information, project statuses, and important documents.
            If you don't know something, say so and offer to help find the answer.
            When discussing clients or projects, provide relevant context from the available information.
            If someone asks about a client or project that isn't in the database yet, explain that you don't have information about it yet and offer to help add it to the database."""}
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

# Slack event handlers
@app.event("message")
def handle_message_events(body, logger):
    """Handle incoming messages."""
    event = body["event"]
    text = event.get("text", "")
    user = event.get("user")
    channel = event.get("channel")
    thread_ts = event.get("thread_ts")
    
    # Log the received message
    logger.info(f"Received message: {text}")
    
    # Ignore bot messages
    if "bot_id" in event:
        logger.info("Ignoring bot message")
        return
    
    # Log mention checks
    logger.info(f"Checking for mentions in: {text}")
    logger.info(f"Contains <@Gilbert AI>: {'<@Gilbert AI>' in text}")
    logger.info(f"Contains <@gilbert ai>: {'<@gilbert ai>' in text}")
    logger.info(f"Contains <@GilbertAI>: {'<@GilbertAI>' in text}")
    logger.info(f"Contains <@U08HPP8UD6Z>: {'<@U08HPP8UD6Z>' in text}")
    logger.info(f"Is thread reply: {bool(thread_ts)}")
    
    # Check if the message is directed at Gilbert AI
    is_mention = "<@Gilbert AI>" in text or "<@gilbert ai>" in text or "<@GilbertAI>" in text or "<@U08HPP8UD6Z>" in text
    
    # If it's a thread reply, check if the parent message is from the bot
    is_bot_thread = False
    if thread_ts:
        try:
            # Get the parent message
            result = app.client.conversations_history(
                channel=channel,
                latest=thread_ts,
                limit=1,
                inclusive=True
            )
            if result["ok"] and result["messages"]:
                parent_message = result["messages"][0]
                is_bot_thread = parent_message.get("bot_id") is not None
        except Exception as e:
            logger.error(f"Error checking thread parent: {e}")
    
    if is_mention or is_bot_thread:
        logger.info("Gilbert AI interaction detected!")
        # Remove the bot mention from the text if present
        clean_text = text.replace("<@Gilbert AI>", "").replace("<@gilbert ai>", "").replace("<@GilbertAI>", "").replace("<@U08HPP8UD6Z>", "").strip()
        logger.info(f"Cleaned text: {clean_text}")
        
        # Get context from previous conversations
        context = ""
        context_data = read_from_sheet("Memory", "A:C")
        if context_data:
            # Get last 5 relevant memories
            relevant_memories = [row[2] for row in context_data[-5:]]
            context = " ".join(relevant_memories)
            logger.info(f"Context from previous conversations: {context}")
        
        # Get AI response
        response = get_ai_response(clean_text, context)
        logger.info(f"AI response: {response}")
        
        # Extract and store important information
        important_info = extract_important_info(clean_text)
        if important_info and SPREADSHEET_ID:  # Only try to store if we have a spreadsheet ID
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            values = [[timestamp, user, important_info]]
            append_to_sheet("Memory", values)
            logger.info(f"Stored important info: {important_info}")
        
        # Send response in thread if it's a thread reply, otherwise as a new message
        if thread_ts:
            app.client.chat_postMessage(
                channel=channel,
                text=response,
                thread_ts=thread_ts
            )
        else:
            app.client.chat_postMessage(
                channel=channel,
                text=response
            )
        logger.info("Response sent successfully")
    
    # Handle specific commands for backward compatibility
    elif "remember" in text:
        try:
            info = text.split("remember", 1)[1].strip()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            values = [[timestamp, user, info]]
            if append_to_sheet("Memory", values):
                app.client.chat_postMessage(
                    channel=channel,
                    text="I've remembered that information! 📝"
                )
            else:
                app.client.chat_postMessage(
                    channel=channel,
                    text="Sorry, I couldn't save that information. Please try again later."
                )
        except Exception as e:
            logger.error(f"Error in remember command: {e}")
            app.client.chat_postMessage(
                channel=channel,
                text="Sorry, I encountered an error while trying to remember that."
            )
    
    elif "recall" in text:
        try:
            topic = text.split("recall", 1)[1].strip()
            data = read_from_sheet("Memory", "A:C")
            if data:
                memories = [row for row in data if topic in row[2].lower()]
                if memories:
                    response = "Here's what I remember about that:\n"
                    for memory in memories[-5:]:
                        response += f"• {memory[2]} (from {memory[1]} on {memory[0]})\n"
                    app.client.chat_postMessage(
                        channel=channel,
                        text=response
                    )
                else:
                    app.client.chat_postMessage(
                        channel=channel,
                        text="I don't have any memories about that topic yet."
                    )
            else:
                app.client.chat_postMessage(
                    channel=channel,
                    text="I don't have any memories stored yet."
                )
        except Exception as e:
            logger.error(f"Error in recall command: {e}")
            app.client.chat_postMessage(
                channel=channel,
                text="Sorry, I encountered an error while trying to recall that information."
            )

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