import os
import boto3
import json
import time

from flask import Blueprint, render_template, request, redirect

from app.models import EditableHTML

main = Blueprint('main', __name__)


@main.route('/')
def index():
    return render_template('main/index.html')


@main.route('/about')
def about():
    editable_html_obj = EditableHTML.get_editable_html('about')
    return render_template(
        'main/about.html', editable_html_obj=editable_html_obj)

# generates all signed URL for AWS upload
@main.route('/sign-s3/')
def sign_s3():
    # Load necessary information into the application
    S3_BUCKET = os.environ.get('S3_BUCKET')
    S3_REGION = os.environ.get('S3_REGION')
    TARGET_FOLDER = 'json/'
    # Load required data from the request
    pre_file_name = request.args.get('file-name')
    if pre_file_name is None:
        return redirect(404)

    file_name = ''.join(pre_file_name.split('.')[:-1]) +\
                str(time.time()).replace('.',  '-') + '.' +  \
                ''.join(pre_file_name.split('.')[-1:])
    file_type = request.args.get('file-type')

    if file_type is None:
        return redirect(404)

    # Initialise the S3 client
    s3 = boto3.client('s3', 'us-east-2')

    # Generate and return the presigned URL
    presigned_post = s3.generate_presigned_post(
        Bucket=S3_BUCKET,
        Key=TARGET_FOLDER + file_name,
        Fields={
            "acl": "public-read",
            "Content-Type": file_type
        },
        Conditions=[{
            "acl": "public-read"
        }, {
            "Content-Type": file_type
        }],
        ExpiresIn=60000)

    # Return the data to the client
    return json.dumps({
        'data':
        presigned_post,
        'url_upload':
        'https://%s.%s.amazonaws.com' % (S3_BUCKET, S3_REGION),
        'url':
        'https://%s.amazonaws.com/%s/json/%s' % (S3_REGION, S3_BUCKET,
                                                 file_name)
    })
