'use strict';

const $ = id => document.getElementById(id);
const escapeHTML = value => String(value).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[ch]));

let groups = [];
let phaseOneAnswers = {};
let phaseTwoAnswers = {};
let phaseOneScores = [];
let selectedPathway = null;
let completing = false;

function hasKey() {
    return /^sk-[A-Za-z0-9_-]{16,509}$/.test($('openaiKey').value.trim());
}

function showError(id, message) {
    $(id).textContent = message;
    $(id).hidden = false;
}

function updatePhaseOne() {
    const count = Object.keys(phaseOneAnswers).length;
    $('answered').textContent = count;
    $('progressFill').style.width = `${count / 20 * 100}%`;
    $('progress').setAttribute('aria-valuenow', count);
    $('remaining').textContent = count === 20
        ? 'All set. Continue to the follow-up questions.'
        : `${20 - count} questions remaining`;
    $('submitBtn').disabled = count !== 20;
}

function renderPhaseOne() {
    $('questionnaire').innerHTML = groups.map((group, index) => `
        <section class="group" aria-label="Question group ${index + 1}">
            <div class="group-header">
                <span class="group-pill">PART ${String(index + 1).padStart(2, '0')} / 06</span>
                <div class="group-line"></div>
            </div>
            ${group.questions.map(q => `
                <div class="question" id="question-${q.id}">
                    <div class="q-head">
                        <span class="q-num">${String(q.id).padStart(2, '0')}</span>
                        <h3 class="q-text" id="label-${q.id}">${escapeHTML(q.text)}</h3>
                    </div>
                    <div class="options" role="radiogroup" aria-labelledby="label-${q.id}">
                        ${q.options.map((label, i) => `
                            <label class="option">
                                <input type="radio" name="q${q.id}"
                                    value="${i}" data-question="${q.id}">
                                <span>${escapeHTML(label)}</span>
                            </label>
                        `).join('')}
                    </div>
                </div>
            `).join('')}
        </section>
    `).join('');

    $('questionnaire').onchange = event => {
        const input = event.target;
        if (input.matches('input[type="radio"][data-question]')) {
            phaseOneAnswers[input.dataset.question] = Number(input.value);
            updatePhaseOne();
        }
    };
    $('actionbar').hidden = false;
    updatePhaseOne();
}

async function init() {
    try {
        const data = await fetchApi('/api/questions');
        groups = data.groups;
        renderPhaseOne();
    } catch (error) {
        $('questionnaire').textContent = 'Could not load the questionnaire.';
        showError('error', error.message);
    }
}

function updatePhaseTwo() {
    const count = Object.values(phaseTwoAnswers).filter(v => v.trim()).length;
    $('deepProgress').style.width = `${count / 6 * 100}%`;
    $('deepRemaining').textContent = count !== 6
        ? `${6 - count} follow-up questions remaining`
        : !hasKey() ? 'Enter your OpenAI API key to continue.'
        : !$('aiConsent').checked ? 'Agree to OpenAI processing to continue.'
        : completing ? 'Generating your reflection…' : 'Ready to view your reflection.';
    $('completeBtn').disabled = completing || count !== 6 || !hasKey() || !$('aiConsent').checked;
}

function renderPhaseTwo(pathway) {
    selectedPathway = pathway;
    $('deepPathwayName').textContent = `Your follow-up focus: ${pathway.name}.`;
    phaseTwoAnswers = {};
    $('openaiKey').value = '';
    $('aiConsent').checked = false;
    $('deepQuestions').innerHTML = pathway.questions.map(q => `
        <div class="question deep-question">
            <div class="q-head">
                <span class="q-num">${q.id}</span>
                <label class="q-text" for="deep-${q.id}">${escapeHTML(q.text)}</label>
            </div>
            <textarea class="reflection-input" id="deep-${q.id}"
                data-deep="${q.id}" rows="3" maxlength="2000"
                placeholder="Write your thoughts here…"></textarea>
        </div>
    `).join('');
    $('deepQuestions').oninput = event => {
        if (event.target.matches('[data-deep]')) {
            phaseTwoAnswers[event.target.dataset.deep] = event.target.value;
            updatePhaseTwo();
        }
    };
    $('workspace').hidden = true;
    $('deepDive').hidden = false;
    updatePhaseTwo();
    $('deepDive').scrollIntoView({behavior: 'smooth', block: 'start'});
}

$('submitBtn').addEventListener('click', async () => {
    const button = $('submitBtn');
    button.disabled = true;
    $('error').hidden = true;
    try {
        const data = await fetchApi('/api/submit', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({answers: phaseOneAnswers})
        });
        phaseOneScores = data.results;
        renderPhaseTwo(data.pathway);
    } catch (error) {
        showError('error', error.message);
    } finally {
        button.disabled = false;
    }
});

$('completeBtn').addEventListener('click', async () => {
    const button = $('completeBtn');
    if (completing || !hasKey() || !$('aiConsent').checked) return;
    completing = true;
    updatePhaseTwo();
    const apiKey = $('openaiKey').value.trim();
    $('openaiKey').value = '';
    $('openaiKey').disabled = true;
    const originalButton = button.innerHTML;
    button.textContent = 'Generating reflection…';
    $('deepError').hidden = true;
    try {
        const data = await submitReflection({
            answers: phaseTwoAnswers,
            share_aggregate: $('shareAggregate').checked,
            ai_consent: $('aiConsent').checked
        }, apiKey);
        $('scoreGrid').innerHTML = data.results.map(r => `
            <article class="score-card">
                <div class="score-top">
                    <span class="score-name">${escapeHTML(r.name)}</span>
                    <span class="score-value">${r.normalized_score} / 100</span>
                </div>
                <div class="score-bar">
                    <div style="width:${r.normalized_score}%"></div>
                </div>
                <div class="score-caption">
                    ${r.raw_score} of ${r.max_score} points · Descriptive score
                </div>
            </article>
        `).join('');
        $('pathwaySummary').textContent =
            `Your reflection focused on ${data.pathway_name.toLowerCase()}. ` +
            'These optional ideas are based on your choices and written responses.';
        $('userReflection').textContent = data.user_reflection;
        $('strategyGrid').innerHTML = data.strategies.map(s => `
            <article class="strategy-card">
                <h4>${escapeHTML(s.title)}</h4>
                <p>${escapeHTML(s.description)}</p>
            </article>
        `).join('');
        $('evaluationSummary').hidden = !Array.isArray(data.evaluation);
        $('evaluationList').innerHTML = (data.evaluation || []).map(point =>
            `<li>${escapeHTML(point)}</li>`
        ).join('');
        $('deepDive').hidden = true;
        $('results').hidden = false;
        $('results').scrollIntoView({behavior: 'smooth', block: 'start'});
    } catch (error) {
        showError('deepError', error.message);
    } finally {
        completing = false;
        $('openaiKey').disabled = false;
        $('openaiKey').value = '';
        button.innerHTML = originalButton;
        updatePhaseTwo();
    }
});

$('restartBtn').addEventListener('click', () => {
    phaseOneAnswers = {};
    phaseTwoAnswers = {};
    phaseOneScores = [];
    selectedPathway = null;
    $('openaiKey').value = '';
    $('aiConsent').checked = false;
    $('shareAggregate').checked = false;
    $('results').hidden = true;
    $('deepDive').hidden = true;
    $('workspace').hidden = false;
    renderPhaseOne();
    $('workspace').scrollIntoView({behavior: 'smooth'});
});

$('openaiKey').addEventListener('input', updatePhaseTwo);
$('aiConsent').addEventListener('change', updatePhaseTwo);
window.addEventListener('pagehide', () => { $('openaiKey').value = ''; });

init();
