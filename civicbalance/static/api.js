'use strict';

function apiConnectionMessage() {
    let message = 'Check that CivicBalance is running at this address, then try again. Your questionnaire answers are still on this page.';
    if (['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname) && window.location.port === '5000') {
        message += ' Port 5000 can be used by macOS AirPlay. Start CivicBalance and open http://127.0.0.1:5050/.';
    }
    return message;
}

function invalidApiResponse(response, description) {
    return Error(`The server returned ${description} (HTTP ${response.status}). ${apiConnectionMessage()}`);
}

async function readApiResponse(response) {
    let text;
    try {
        text = await response.text();
    } catch (_) {
        throw invalidApiResponse(response, 'an incomplete response');
    }
    if (!text.trim()) throw invalidApiResponse(response, 'an empty response');
    let data;
    try {
        data = JSON.parse(text);
    } catch (_) {
        throw invalidApiResponse(response, 'an incomplete or non-JSON response');
    }
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
        throw invalidApiResponse(response, 'an unexpected response');
    }
    if (!response.ok) {
        throw Error(typeof data.error === 'string' ? data.error : `CivicBalance could not complete this request (HTTP ${response.status}).`);
    }
    return data;
}

async function fetchApi(url, options) {
    let response;
    try {
        response = await fetch(url, options);
    } catch (_) {
        throw Error(`Could not connect to CivicBalance. ${apiConnectionMessage()}`);
    }
    return readApiResponse(response);
}

async function submitReflection(payload, apiKey) {
    // Verify the local app before sending a key to a service at the wrong port.
    const health = await fetchApi('/api/health', {cache: 'no-store'});
    if (health.application !== 'CivicBalance' || health.status !== 'ok') {
        throw Error(`This address is not responding as CivicBalance. ${apiConnectionMessage()}`);
    }
    return fetchApi('/api/complete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-OpenAI-Key': apiKey},
        body: JSON.stringify(payload)
    });
}
