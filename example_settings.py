# Used to protect session cookie (for wtforms and auth)
# give a random string, at best generated by import secrets; print(secrets.token_bytes(16))
SECRET_KEY = None

# Base URL of the system, used in SMS and emails
BASE_URL = 'http://127.0.0.1:5000'

# SMTP details
SMTP_HOST = '127.0.0.1'
SMTP_FROM = 'impfzentrum@example.com'
SMTP_ERROR_TO = ['devops@example.com']

# All messages sent by regular operation, i.e. without the error messages, are BCCed to these addresses
SMTP_BCC = SMTP_ERROR_TO

# Message templates
SMTP_LOGIN_TEMPLATE = {
    'subject': 'Impftermin für besonders berechtigte Gruppen (innerhalb §2 CoronaImpfV)',
    'body': """Bitte geben Sie unter {base_url}/login-availability/{token} Ihre Verfügbarkeit für eine Impfung an.

Sie werden sobald wie möglich einen Impftermin nach Ihrer Verfügbarkeit vorgeschlagen bekommen. 

Vielen Dank, Ihr Impfzentrum Klinikum Stuttgart"""}