import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ['APP_PIN'] = '123456'
import main

client = TestClient(main.app)


def test_home_and_mobile_assets():
    assert client.get('/').status_code == 200
    assert 'KaiClip Studio' in client.get('/').text
    conf = client.get('/api/config').json()
    assert conf['pin_required'] is True
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/static/manifest.webmanifest').status_code == 200


def test_api_validation():
    upload = {'video': ('test.mp4', b'not-video', 'video/mp4')}
    data = {'authorized': 'false'}
    assert client.post('/api/jobs', files=upload, data=data).status_code == 401
    assert client.post('/api/jobs', files=upload, data=data, headers={'X-App-Pin': '123456'}).status_code == 400
    data['authorized'] = 'true'
    data['count'] = '99'
    assert client.post('/api/jobs', files=upload, data=data, headers={'X-App-Pin': '123456'}).status_code == 400
    assert client.get('/api/jobs/wrongid', headers={'X-App-Pin': '123456'}).status_code == 404
    assert client.get('/api/jobs/wrongid/files/clip_01.mp4').status_code == 404
