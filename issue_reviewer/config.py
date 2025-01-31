

# max. number of files to return in the search
MAX_FILE_SEARCH_RESULTS = 100
# number of lines above and below the focus line to show in the editor view
WINDOW_BUFFER = (5, 95)

RECURSION_LIMIT = 25

# max output lines when running a python file
MAX_OUTPUT_LINES = 50

# increment `end_line` a few times to try to get a valid edit from the model - risk that if this is too high, it could eliminate lines in an unexpected way
MAX_RETRIES_EDIT_FILE = 3

BEDROCK_MODEL_ID = "us.anthropic.claude-3-5-sonnet-20240620-v1:0"
BEDROCK_MODEL_ID_LIGHT = "us.anthropic.claude-3-5-haiku-20241022-v1:0"

MODEL_TEMPERATURE = 0.2
BOTO3_MAX_ATTEMPTS = 1000
LANGCHAIN_STOP_AFTER_ATTEMPT = 10

SNIPPET_LINES = 4

RETRIEVE_K_DOCS = 15
MAX_CHARACTERS_PER_DOC = 2048  # don't change this