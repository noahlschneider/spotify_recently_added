# Overview

This project is an **AWS Lambda function that automatically maintains "Recently Added" style playlists in Spotify**, similar to the "Recently Added" "Smart Playlist" feature you may remember from iTunes.

The Lambda runs on a schedule (default: every 15 minutes) and:
- Looks at your "Liked Songs" library, sorted by most recently added
- Splits that list into chunks (default: 3 playlists, 200 songs each)
- Keeps each chunk in sync with a dedicated playlist (these names are defaults):
  - **Recently Added** → your latest songs
  - **Older Recently Added** → the next batch
  - **Even Older Recently Added** → the next batch after that

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
3. Set your Redirect URI (I suggest: `http://127.0.0.1:8000/callback`) for authentication.

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

2. Note the secret ARN.

## Run Script Locally

You need to run the function once locally to open a window to login to Spotify and give the app access to your library. Once authenticated, your token shouldn't expire unless you go a long period of time between running the app. If that happens, just run the script again locally.

1. Install dependencies:
```bash
pip install -r requirements.txt
```
2. Set any [environmental variables](#environment-variables) you desire.

3. Run Python function:
```bash
python app/lambda_function.py
```

4. Authenticate in the window that appears and ensure code finishes successfully.

## Create Lambda Layer

The Lambda function requires `spotipy` and `boto3`. Create a Lambda layer with them installed.

1. Install dependencies and ZIP them:
```bash
pip install spotipy boto3 -t python/
zip -r spotipy_boto3.zip python/
```

2. Create a new Lambda layer (default name: `spotipy_boto3`) by uploading ZIP and setting runtime to Python 3.13.

AWS CLI equivalent:
```bash
aws lambda publish-layer-version --layer-name spotipy_boto3 --zip-file \
fileb://spotipy_boto3.zip --compatible-runtimes python3.13
```

3. Note the layer ARN — you’ll need it when creating the Lambda function.

## Create IAM Role

1. Create IAM role (default name: `recently_added_spotipy_role`) using policy document:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "lambda.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
```

2. Note the role ARN — you’ll need it when creating the Lambda function.

3. Attach `SecretsManagerReadWrite` and `AWSLambdaBasicExecutionRole` policies to your role.

AWS CLI equivalent:
```bash
aws iam create-role --role-name recently_added_spotipy_role --assume-role-policy-document '{"Version": "2012-10-17","Statement": [{ "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}'

aws iam attach-role-policy --role-name recently_added_spotipy_role --policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrit

aws iam attach-role-policy --role-name recently_added_spotipy_role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```

## Create Lambda Function

1. ZIP up app:
```bash
zip -r recently_added_spotipy.zip -j app/*
```

2. Create Lambda function (default name: `spotify_recently_added`) by uploading ZIP. You'll also need to set the following configurations:
    - Runtime settings -> Runtime: `python3.13`.
    - Runtime settings -> Layers:  select Lambda layer you created in previous step. Also add [Powertools for AWS Lambda (Python)](https://docs.powertools.aws.dev/lambda/python/latest/#lambda-layer_1).
    - Configuration -> General configuration -> Timeout:  set to 15 minutes (or however often you'll run the function).
    - Configuration -> Permissions -> Execution Role: select role you created in previous step.
    - Configuration -> Environmental variables: any [environmental variables](#environment-variables) you desire.
    - Configuration -> Concurrency and recursion detection -> Reserved concurrency: set to 1 so multiple functions can't run at the time time.
    - Configuration -> Asynchronous invocation -> Retries: set maximum age of event to 1 minute and retry attempts to 0.
    
AWS CLI equivalent:
```bash
aws lambda create-function --function-name recently_added_spotipy --runtime python3.13 --zip-file fileb://recently_added_spotipy --layers <layer ARN from previous step> <AWS Powertools ARN> --role <role ARN from previous step> --timeout 900

aws lambda put-function-concurrency --function-name recently_added_spotipy --reserved-concurrent-executions 1

aws lambda put-function-event-invoke-config --function-name recently_added_spotipy --maximum-retry-attempts 0 --maximum-event-age-in-seconds 60
```

# Set Up Lambda Schedule

1. Add trigger to Lambda by going to Configuration -> Triggers -> Add trigger. Select EventBridge (CloudWatch Events), create new role.
    - Rule name (default): `recently_added_spotipy_schedule`.
    - Schedule expression (default): rate(15 minutes).

AWS CLI equivalent:
```bash
aws events put-rule --name recently_added_spotipy_schedule --schedule-expression "rate(15 minutes)"

aws lambda add-permission --function-name recently_added_spotipy --statement-id recently_added_spotipy_schedule_permission --action "lambda:InvokeFunction" --principal events.amazonaws.com --source-arn <schedule ARN>

aws events put-targets --rule recently_added_spotipy_schedule --targets "Id"="recently_added_spotipy_trigger","Arn"="<lambda ARN>"
```

# Environment Variables

| Variable          | Default Value                    | Description                                                                                  |
|-------------------|----------------------------------|----------------------------------------------------------------------------------------------|
| `PLAYLIST_NAMES`  | `["Recently Added", "Older Recently Added", "Even Older Recently Added"]` | Names of playlists  to create or sync (JSON array). |
| `PLAYLIST_LENGTH` | `200`                            | Number of tracks in each playlist.                                                           |
| `OAUTH_SECRET`    | `spotipy-oauth`                  | Secrets Manager secret storing Spotify `client_id` and `client_secret`.                      |
| `TOKEN_SECRET`    | `spotipy-token`                  | Secrets Manager secret used by Spotipy to cache OAuth tokens.                                |
| `AWS_REGION`      | `us-east-2`                      | AWS region for Lambda, Secrets Manager, etc.                                                 |
| `REDIRECT_URI`    | `http://127.0.0.1:8000/callback` | Redirect URI for Spotify OAuth. Must match the value in your Spotify app.                    |
