'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function api(port = '5050', fetchImpl = async () => { throw Error('Unexpected request'); }) {
    const context = vm.createContext({
        window: {location: {hostname: '127.0.0.1', port}},
        fetch: fetchImpl,
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/api.js'), 'utf8'), context);
    return context;
}

function response(text, status = 200) {
    return {status, ok: status >= 200 && status < 300, text: async () => text};
}

test('successful API JSON is returned', async () => {
    const value = await api().readApiResponse(response('{"application":"CivicBalance"}'));
    assert.equal(value.application, 'CivicBalance');
});

test('empty AirPlay-like 403 explains the port conflict', async () => {
    await assert.rejects(api('5000').readApiResponse(response('', 403)), error => {
        assert.match(error.message, /empty response.*HTTP 403/);
        assert.match(error.message, /AirPlay/);
        assert.match(error.message, /127\.0\.0\.1:5050/);
        assert.doesNotMatch(error.message, /Unexpected end of JSON/);
        return true;
    });
});

test('HTML and truncated JSON produce helpful errors without echoing response bodies', async () => {
    for (const text of ['<html>private provider diagnostic</html>', '{"user_reflection":']) {
        await assert.rejects(api().readApiResponse(response(text, 502)), error => {
            assert.match(error.message, /incomplete or non-JSON response.*HTTP 502/);
            assert.doesNotMatch(error.message, /private provider diagnostic|user_reflection/);
            return true;
        });
    }
});

test('a partially received response body is handled', async () => {
    await assert.rejects(api().readApiResponse({
        status: 200, ok: true, text: async () => { throw Error('private details'); },
    }), /incomplete response/);
});

test('JSON error messages from the API remain visible', async () => {
    await assert.rejects(api().readApiResponse(response('{"error":"OpenAI did not accept this API key."}', 401)),
        /OpenAI did not accept this API key/);
});

test('unexpected JSON types are rejected', async () => {
    for (const text of ['null', '[]', '"not an API response"']) {
        await assert.rejects(api().readApiResponse(response(text)), /unexpected response/);
    }
});

test('connection failures explain how to recover without exposing error details', async () => {
    const client = api('5050', async () => { throw Error('sensitive transport context'); });
    await assert.rejects(client.fetchApi('/api/questions'), error => {
        assert.match(error.message, /Could not connect to CivicBalance/);
        assert.match(error.message, /answers are still on this page/);
        assert.doesNotMatch(error.message, /sensitive transport/);
        return true;
    });
});

test('a key is not sent when the server responds with empty health JSON', async () => {
    const requests = [];
    const client = api('5000', async (url, options) => {
        requests.push({url, options});
        return response('', 403);
    });
    await assert.rejects(client.submitReflection({answers: {}}, 'synthetic-not-a-real-key'), /AirPlay/);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/health');
    assert.equal(requests[0].options.headers, undefined);
});

test('a key is not sent to a different application', async () => {
    const requests = [];
    const client = api('5050', async (url, options) => {
        requests.push({url, options});
        return response('{"application":"Different App","status":"ok"}');
    });
    await assert.rejects(client.submitReflection({}, 'synthetic-not-a-real-key'), /not responding as CivicBalance/);
    assert.equal(requests.length, 1);
});

test('a valid CivicBalance health response allows the requested completion', async () => {
    const requests = [];
    const client = api('5050', async (url, options) => {
        requests.push({url, options});
        return response(url === '/api/health'
            ? '{"application":"CivicBalance","status":"ok"}'
            : '{"user_reflection":"Synthetic result"}');
    });
    const payload = {answers: {'21': 'Synthetic answer'}, share_aggregate: false, ai_consent: true};
    const result = await client.submitReflection(payload, 'synthetic-not-a-real-key');
    assert.equal(result.user_reflection, 'Synthetic result');
    assert.equal(requests[1].url, '/api/complete');
    assert.equal(requests[1].options.headers['X-OpenAI-Key'], 'synthetic-not-a-real-key');
    assert.deepEqual(JSON.parse(requests[1].options.body), payload);
});
