import streamlit as st
import vertexai
from vertexai import agent_engines
import uuid # For generating unique user IDs
import json # Added for parsing service account JSON
from google.oauth2 import service_account # Added for creating credentials object

# --- 1. Configuration Loading (from st.secrets) ---
try:
    PROJECT_ID = st.secrets["gcp"]["project_id"]
    LOCATION = st.secrets["gcp"]["location"]
    STAGING_BUCKET_NAME = st.secrets["gcp"]["staging_bucket_name"]
    AGENT_RESOURCE_ID = st.secrets["agent"]["resource_id"]
except KeyError as e:
    st.error(f"Missing configuration in st.secrets: {e}. Please check your .streamlit/secrets.toml")
    st.stop()

# --- 2. Vertex AI Initialization & Agent Retrieval (Cached) ---
@st.cache_resource # Cache the resource so it's not reloaded on every script run
def get_financial_agent():
    print("Attempting to initialize get_financial_agent...")
    
    creds = None
    gcp_creds_json_str = None 
    
    try:
        # Try to get the gcp section, and then the service_account_credentials_json from it
        gcp_secrets = st.secrets.get("gcp", {})
        gcp_creds_json_str = gcp_secrets.get("service_account_credentials_json")
        
        if gcp_creds_json_str:
            print("Found gcp.service_account_credentials_json in secrets. Attempting to load.")
            creds_info = json.loads(gcp_creds_json_str)
            creds = service_account.Credentials.from_service_account_info(creds_info)
            print("Successfully loaded service account credentials from secrets.")
        else:
            print("gcp.service_account_credentials_json not found in st.secrets. Using default credentials (e.g., for local development or GCE environment).")
    except KeyError:
        # This specific KeyError might not be hit due to .get with default, but kept for safety
        print("gcp.service_account_credentials_json not found in st.secrets (KeyError). Using default credentials.")
    except json.JSONDecodeError as e:
        print(f"Error decoding service_account_credentials_json: {e}. Using default credentials.")
        st.warning(f"Failed to parse service account JSON from secrets: {e}. Ensure it's a valid JSON string. Falling back to default credentials.")
    except Exception as e: # Catch other potential errors from google.oauth2.service_account
        print(f"Error loading service account credentials from secrets: {e}. Using default credentials.")
        st.warning(f"Could not load service account from JSON secret: {e}. Falling back to default credentials.")

    try:
        print(f"Using PROJECT_ID: {PROJECT_ID}, LOCATION: {LOCATION}, STAGING_BUCKET: gs://{STAGING_BUCKET_NAME}")
        if creds:
            print("Attempting vertexai.init() with explicit credentials loaded from secrets...")
            vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=f"gs://{STAGING_BUCKET_NAME}", credentials=creds)
        else:
            print("Attempting vertexai.init() with default/ambient credentials...")
            vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=f"gs://{STAGING_BUCKET_NAME}")
        print("vertexai.init() successful.")
    except Exception as e_init:
        print(f"ERROR during vertexai.init(): {e_init}") # For debug
        st.error(f"Failed during Vertex AI initialization: {e_init}")
        raise # Re-raise the exception to stop execution if init fails

    try:
        print(f"Attempting agent_engines.get() with AGENT_RESOURCE_ID: {AGENT_RESOURCE_ID}") # For debug
        agent = agent_engines.get(AGENT_RESOURCE_ID)
        print(f"Agent '{agent.name}' retrieved successfully.") # For debug
        return agent
    except Exception as e_get_agent:
        print(f"ERROR during agent_engines.get(): {e_get_agent}") # For debug
        st.error(f"Failed to get agent: {e_get_agent}")
        raise # Re-raise the exception

try:
    agent = get_financial_agent()
except Exception as e:
    st.error(f"Failed to initialize Vertex AI or get agent: {e}")
    st.stop()

# --- 3. Session State Management ---
if "agent_user_id" not in st.session_state:
    st.session_state.agent_user_id = str(uuid.uuid4())
    print(f"Generated agent_user_id: {st.session_state.agent_user_id}") # For debug

if "agent_session_id" not in st.session_state:
    try:
        print(f"Creating new agent session for user_id: {st.session_state.agent_user_id}") # For debug
        session_info = agent.create_session(user_id=st.session_state.agent_user_id)
        st.session_state.agent_session_id = session_info["id"]
        print(f"Agent session created: {st.session_state.agent_session_id}") # For debug
    except Exception as e:
        st.error(f"Failed to create agent session: {e}")
        # Potentially clear related session state if creation fails to allow retry
        if "agent_user_id" in st.session_state: del st.session_state.agent_user_id
        st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = [] # Store as list of dicts: {"role": "user/assistant", "content": "..."}

# --- 4. UI Layout ---
st.title("ChatAnalyst: Report Agent")
st.markdown("*Say hello to get started. If you encounter an error, ask to try again.*")

# Display existing chat messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- 5. Handle User Input and Agent Interaction ---
if prompt := st.chat_input("Ask the financial advisor..."):
    # Add user message to chat history and display it
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Get agent response
    with st.chat_message("assistant"):
        response_placeholder = st.empty() # For streaming effect
        full_response_parts = []
        try:
            print(f"Streaming query for session: {st.session_state.agent_session_id}, message: '{prompt}'") # For debug
            for event in agent.stream_query(
                user_id=st.session_state.agent_user_id,
                session_id=st.session_state.agent_session_id,
                message=prompt
            ):
                # print(f"DEBUG Event: {event}") # Uncomment for very detailed event logging
                if "content" in event and "parts" in event["content"]:
                    for part in event["content"]["parts"]:
                        if "text" in part:
                            text_part = part["text"]
                            full_response_parts.append(text_part)
                            # Update placeholder with accumulating text + typing indicator "▌"
                            response_placeholder.markdown("".join(full_response_parts) + "▌")
            
            final_response = "".join(full_response_parts)
            # Escape dollar signs for proper display
            final_response = final_response.replace("$", "\\$")
            if not final_response and not full_response_parts: # If no text parts were received at all
                 print("No text parts received from agent stream_query.") # For debug
                 final_response = "Sorry, I encountered an issue and couldn't get a response. Please check the logs or try again."
            
            response_placeholder.markdown(final_response) # Display final response
            st.session_state.messages.append({"role": "assistant", "content": final_response})

        except Exception as e:
            error_message = f"Error querying agent: {e}"
            st.error(error_message)
            print(error_message) # For debug
            st.session_state.messages.append({"role": "assistant", "content": f"Error: Could not get a response. Details: {e}"})

# --- 6. (Optional) Session Cleanup ---
# This part is more advanced for Streamlit. For now, sessions will expire on the backend.
# A button to manually clear session_state could be added for testing:
# if st.button("Clear Session and Restart"):
#    for key in list(st.session_state.keys()):
#        del st.session_state[key]
#    st.rerun() 
# Random change