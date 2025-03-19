# Creative Agency Slack Bot

A powerful Slack bot for creative agencies that helps manage client communications, onboarding, and file deliveries using Google Sheets as a backend.

## Features

- **Client Information Storage**: Store and retrieve client information using Google Sheets
- **Onboarding Automation**: Guide new clients through the onboarding process
- **File Delivery**: Send and track file deliveries to clients
- **Update Communication**: Send automated updates to clients about project progress
- **Client Portal**: Provide clients with easy access to their project information
- **AI Integration**: Get AI-powered responses for creative tasks and client communication

## Setup Instructions

1. **Slack App Setup**:
   - Go to [api.slack.com/apps](https://api.slack.com/apps)
   - Click "Create New App"
   - Choose "From scratch"
   - Name your app and select your workspace
   - Under "OAuth & Permissions", add the following bot token scopes:
     - `chat:write`
     - `im:write`
     - `channels:read`
     - `channels:join`
     - `files:write`
     - `files:read`
   - Install the app to your workspace
   - Copy the Bot User OAuth Token and Signing Secret

2. **Google Sheets Setup**:
   - Go to [Google Cloud Console](https://console.cloud.google.com)
   - Create a new project
   - Enable the Google Sheets API
   - Create credentials (Service Account)
   - Download the JSON key file
   - Create a Google Sheet and share it with the service account email
   - Copy the spreadsheet ID from the URL

3. **Railway Setup**:
   - Create a new project on [Railway.app](https://railway.app)
   - Connect your GitHub repository
   - Add the following environment variables:
     ```
     SLACK_BOT_TOKEN=xoxb-your-bot-token
     SLACK_SIGNING_SECRET=your-signing-secret
     SLACK_APP_TOKEN=xapp-your-app-token
     OPENAI_API_KEY=your-openai-api-key
     GOOGLE_SERVICE_ACCOUNT={"type": "service_account", ...} # Your entire service account JSON
     SPREADSHEET_ID=your-spreadsheet-id
     ```

4. **Google Sheets Structure**:
   Create the following sheets in your Google Sheet:
   - `Memory`: For storing and retrieving information
   - `Onboarding`: Track client onboarding progress
   - `Clients`: Basic client information
   - `Deliverables`: File delivery tracking
   - `Updates`: Client communication history

## Usage

### Basic Commands

- `@bot remember [information]` - Store information in the Google Sheet
- `@bot recall [topic]` - Retrieve stored information
- `@bot onboard [client name]` - Start the onboarding process
- `@bot ask [question]` - Get AI-powered responses

### Google Sheets Structure

The bot uses the following sheets:
- `Clients`: Basic client information
- `Onboarding`: Onboarding progress and documents
- `Deliverables`: File delivery tracking
- `Updates`: Client communication history
- `Memory`: General information storage

## Security Notes

- Never commit your `.env` file or Google credentials
- Keep your bot token secure
- Regularly rotate credentials
- Monitor bot usage and permissions

## Support

For support or questions, please contact your system administrator. 