# Overview

This project is an **AWS Lambda function that automatically maintains "Recently Added" style playlists in Spotify**, similar to the "Recently Added" "Smart Playlist" feature you may remember from iTunes.

The Lambda runs on a schedule (default: every 15 minutes) and:
- Looks at your "Liked Songs" library, sorted by most recently added.
- Splits that list into chunks (default: 3 playlists, 200 songs each).
- Keeps each chunk in sync with a dedicated playlist:
  - **Recently Added** → your latest songs.
  - **Older Recently Added** → the next batch.
  - **Even Older Recently Added** → the next batch after that.

This creates a rolling set of playlists that automatically refresh as you add new music to your library.

The implementation uses:
- **AWS Lambda** for execution
- **AWS Secrets Manager** to store Spotify credentials and cached tokens
- **AWS EventBridge (CloudWatch events)** for scheduling
- **[Spotipy](https://spotipy.readthedocs.io/en/)** (Python client for the Spotify Web API) for playlist management
- **AWS Lambda Powertools** for structured logging

It’s lightweight, serverless, and (once deployed) completely hands-off.

# Setup

## Prerequisites

Make sure you have:

- An **AWS account** with permissions for Lambda, Secrets Manager, and EventBridge.
- A **Spotify Developer account** to create an app and get your credentials.
- **Python 3.13+** installed, and a virtual environment if desired.
- **AWS CLI** instealled and configured.

## Spotify App & Credentials

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/) and create a new app.
2. Copy your **Client ID** and **Client Secret**.
3. Set your Redirect URI (e.g., `http://localhost:8888/callback`) for authentication.


## AWS Secrets Manager Secret Creation

Secrets Manager is used to store Spotify credentials and the OAuth token.

1. Create a new secret (default name: `spotipy-oauth`) with the following JSON structure:

```json
{
  "SPOTIFY_CLIENT_ID": "<your client id>",
  "SPOTIFY_CLIENT_SECRET": "<your client secret>"
}
```

AWS CLI equivalent:
```bash
aws secretsmanager create-secret --name spotipy-oauth --secret-string \
'{"client_id": "<your client id>", "client_secret": <your client secret>"}'
```

2. Note the Secret ARN.



## Run Script Locally

You need to run the function once locally to open a window to login to Spotify and give the app access to your library. Once authenticated, your token shouldn't expire unless you go a long period of time between running the app.

1. Install dependencies:
```bash
pip install -r requirements.txt -t ./package
```
2. Set any [environmental variables](#environment-variables) you desire.

3. Run Python function:
```bash
python app/lambda_function.py
```

4. Authenticate in the window that appears and ensure code finishes successfully.

## Create Lambda Layer

The Lambda function requires `spotipy` and `boto3` installed.

1. Install dependencies and ZIP them
 ```bash
pip install spotipy boto3 -t python/
zip -r spotipy_boto3.zip python/
```

2. Create a new Lambda layer (default name: `spotipy_boto3`) by uploading ZIP and setting runtime to Python 3.13

AWS CLI equivalent:
```bash
aws lambda publish-layer-version --layer-name $spotipy_boto3 --zip-file \
fileb://spotipy_boto3.zip --compatible-runtimes python3.13
```

3. Note the Secret ARN — you’ll need it for Lambda environment variables.

# Create Lambda Function

# TODO:


# Environment Variables

| Variable          | Default Value                    | Description                                                                                  |
|-------------------|----------------------------------|----------------------------------------------------------------------------------------------|
| `PLAYLIST_NAMES`  | `["Recently Added", "Older Recently Added", "Even Older Recently Added"]` | Names of playlists  to create or sync (JSON array). |
| `PLAYLIST_LENGTH` | `200`                            | Number of tracks in each playlist.                                                           |
| `OAUTH_SECRET`    | `spotipy-oauth`                  | Secrets Manager secret storing Spotify `client_id` and `client_secret`.                      |
| `TOKEN_SECRET`    | `spotipy-token`                  | Secrets Manager secret used by Spotipy to cache OAuth tokens.                                |
| `AWS_REGION`      | `us-east-2`                      | AWS region for Lambda, Secrets Manager, etc.                                                 |
| `REDIRECT_URI`    | `http://127.0.0.1:8000/callback` | Redirect URI for Spotify OAuth. Must match the value in your Spotify app.                    |

----------------------------

# OLD

## 1. Configuration
Customize as necessary.
```
CLIENT_ID="foo"
CLIENT_SECRET="bar"
REDIRECT_URI="http://127.0.0.1:8000/callback"
REGION="us-east-2"
POWER_TOOLS_LAYER_ARN="arn:aws:lambda:us-east-2:017000801446:layer:AWSLambdaPowertoolsPythonV3-python313-x86_64:21"
RATE="15 minutes"
RATE_SECONDS=900
APP_NAME="recently_added_spotipy"
VIRTUAL_ENV_NAME=$APP_NAME
OAUTH_SECRET_ID="spotipy-oauth"
LAYER_NAME="spotipy_boto3"
ROLE_NAME="${APP_NAME}_role"
SCHEDULE_NAME="${APP_NAME}_schedule"
PERMISSION_NAME="${SCHEDULE_NAME}_permission"
TRIGGER_NAME="${SCHEDULE_NAME}_trigger"
````
- Rate syntax is [documented here](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-scheduled-rule-pattern.html#eb-rate-expressions)
- Powertool ARN for your region [available here](https://docs.powertools.aws.dev/lambda/python/latest/#lambda-layer)

## 2. Setup Python Virtual Env
I use [pyenv-virtualenv](https://github.com/pyenv/pyenv-virtualenv]). Feel free to use something else
```
# Install Python 3.13
pyenv install 3.13

# Create virtual env
pyenv virtualenv 3.13 $VIRTUAL_ENV_NAME

# Activate the virtual env
pyenv activate spotipy

# Install requirements
pip install -r requirements.txt 
```

## 3. Run Script Locally
```
python app/lambda_function.py
```
You need to run the function once locally to get prompted to login to Spotify and give the app access to your library. Once authenticated, your token shouldn't expire unless you go a long period of time between running the app.

## 4. Create Lambda Layer
```
# Install spotipy & boto3 to python/
pip install spotipy boto3 -t python/

# Create ZIP of python/
zip -r $LAYER_NAME.zip python/

# Create & upload Lambda layer from ZIP, store ARN
LAYER_ARN=$(aws lambda publish-layer-version --layer-name $LAYER_NAME --zip-file fileb://$LAYER_NAME.zip --compatible-runtimes python3.13 --query 'LayerVersionArn' --output text --region $REGION)
```

## 5. IAM Role & Policies
```
# Create IAM role, store ARN
ROLE_ARN=$(aws iam create-role --role-name $ROLE_NAME --assume-role-policy-document '{"Version": "2012-10-17","Statement": [{ "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}' --query 'Role.Arn' --output text --region $REGION)

# Wait for role to exist
aws iam wait role-exists --role-name $ROLE_NAME --region $REGION

# Attach SecretsManagerReadWrite policy
aws iam attach-role-policy --role-name $ROLE_NAME --policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrite --region $REGION

# Attach AWSLambdaBasicExecutionRole policy
aws iam attach-role-policy --role-name $ROLE_NAME --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole --region $REGION
```

# 5. Create OAuth Secret
```
# Create secret TODO: test
aws secretsmanager create-secret --name OAUTH_SECRET_ID --secret-string '{"client_id": 
"$CLIENT_ID", "client_secret": "$CLIENT_SECRET"}'
```

## 6. Create Lambda
```
# Create ZIP for app/
zip -r $APP_NAME$.zip -j app/*

# Create Lambda function, store ARN
LAMBDA_ARN=$(aws lambda create-function --function-name $APP_NAME --runtime python3.13 --zip-file fileb://$APP_NAME.zip --layers $POWER_TOOLS_LAYER_ARN $LAYER_ARN --role $ROLE_ARN --handler lambda_function.lambda_handler --timeout $RATE_SECONDS --query 'FunctionArn' --output text --region $REGION)

# Set concurrency to 1
aws lambda put-function-concurrency --function-name $APP_NAME --reserved-concurrent-executions 1 --region $REGION

# Set to don't auto retry
aws lambda put-function-event-invoke-config --function-name $APP_NAME --maximum-retry-attempts 0 --maximum-event-age-in-seconds 60
```

# 7. Set Up Lambda Schedule

```
# Create schedule event, store ARN
SCHEDULE_ARN=$(aws events put-rule --name $SCHEDULE_NAME --schedule-expression "rate($RATE)" --query 'RuleArn' --output text --region $REGION)

# Give permissions for schedule event to execute Lambda
aws lambda add-permission --function-name recently_added_spotipy --statement-id $PERMISSION_NAME --action "lambda:InvokeFunction" --principal events.amazonaws.com --source-arn $SCHEDULE_ARN --no-cli-pager --region $REGION

# Add schedule event trigger for Lambda
aws events put-targets --rule $SCHEDULE_NAME --targets "Id"="$TRIGGER_NAME","Arn"="$LAMBDA_ARN" --no-cli-pager --region $REGION
```