'use strict';

const domainNames = {
    A: 'News Fatigue',
    B: 'Compassion Fatigue',
    C: 'Affective Polarization',
    D: 'Motivated Reasoning',
    E: 'Spiral of Silence',
    F: 'Political Cynicism'
};

const pathwayNames = {
    1: 'Sensory Overload & Emotional Exhaustion',
    2: 'Social Polarization & Digital Isolation',
    3: 'Cognitive Rigidity & Civic Helplessness'
};

const $ = id => document.getElementById(id);

$('loadReport').addEventListener('click', async () => {
    $('reportError').hidden = true;
    $('reportEmpty').hidden = true;
    $('reportContent').hidden = true;
    try {
        const data = await fetchApi('/api/report', {
            headers: {'X-Report-Token': $('token').value},
            cache: 'no-store'
        });
        $('token').value = '';
        if (!data.ready) {
            $('reportEmpty').textContent =
                `The report requires at least ${data.minimum} voluntary responses. ` +
                `Currently ${data.count} received.`;
            $('reportEmpty').hidden = false;
            return;
        }
        $('responseCount').textContent = data.count;
        $('domainGrid').innerHTML = Object.entries(data.domain_means).map(([id, score]) => `
            <article class="score-card">
                <div class="score-top">
                    <span class="score-name">${domainNames[id]}</span>
                    <span class="score-value">${score} / 100</span>
                </div>
                <div class="score-bar"><div style="width:${score}%"></div></div>
                <div class="score-caption">Average self-reported friction score</div>
            </article>
        `).join('');
        $('pathwayGrid').innerHTML = Object.entries(data.pathway_counts).map(([id, count]) => `
            <article class="score-card">
                <div class="score-name">${pathwayNames[id]}</div>
                <div class="score-value report-pathway-count">
                    ${count === null ? 'Suppressed' : count}
                </div>
                <div class="score-caption">
                    ${count === null ? `Below reporting minimum (${data.minimum})` : 'Completed follow-up responses'}
                </div>
            </article>
        `).join('');
        $('reportContent').hidden = false;
    } catch (error) {
        $('reportError').textContent = error.message;
        $('reportError').hidden = false;
    }
});
