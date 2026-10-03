// Full user journey through the real CrewBot UI in Chromium.
// Usage: python tests/e2e/server.py 8790 & node tests/e2e/run.js http://127.0.0.1:8790
// Needs Playwright (npm i -g playwright) and Chromium. Screenshots go to SCREENSHOTS or the OS temp folder.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const BASE = process.argv[2] || 'http://127.0.0.1:8790';
const OUT = process.env.SCREENSHOTS || require('os').tmpdir();
const results = [];
function check(name, ok, detail = '') { results.push({ name, ok: !!ok, detail }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? ' — ' + detail : '')); }

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|Content Security Policy|Blocked script|sandboxed/i.test(m.text())) errors.push('console: ' + m.text()); });
  const nav = async view => { await page.click(`nav button[data-view="${view}"]`); await page.waitForTimeout(300); };

  await page.goto(BASE + '/app');
  await page.waitForSelector('#company-label');
  check('Workspace loads with Mentor only', (await page.locator('#chat-owner option').count()) === 1);

  // 1. Mentor onboarding through chat (tool loop saves business profile).
  await page.fill('#message', 'We are Fixture Bakery, artisan bread and office catering in Austin. Goal: 20 office clients.');
  await page.click('#send-message');
  await page.waitForFunction(() => document.querySelector('#company-label').textContent.includes('Fixture Bakery'), null, { timeout: 20000 });
  check('Mentor saved the business profile from chat', true);

  // 2. Hire experts from the roster.
  await nav('team');
  await page.waitForSelector('#expert-roster');
  check('Expert roster lists 9 experts', (await page.locator('#expert-roster .expert-card').count()) === 9);
  for (const name of ['Iris', 'Nova', 'Echo']) {
    await page.locator('#expert-roster .expert-card', { hasText: name }).getByRole('button').first().click();
    await page.waitForTimeout(500);
  }
  await page.waitForFunction(() => document.querySelectorAll('#employees-grid .employee-card').length === 4);
  check('Designer, engineer and marketer hired', true);
  const chips = await page.locator('#employees-grid .employee-card', { hasText: 'Iris' }).locator('.skill-chip').allTextContents();
  check('Designer card shows its skills', chips.includes('Logos') && chips.includes('Videos'), chips.join(', '));
  await page.screenshot({ path: OUT + '/01-team.png', fullPage: true });

  // 3. Brand kit in settings.
  await nav('settings');
  await page.fill('textarea[name=visual_style]', 'Warm, hand-crafted, flat illustration, terracotta and navy');
  await page.click('.appearance-panel button[type=submit]');
  await page.waitForTimeout(600);
  check('Settings exposes image/video model and budget fields', await page.locator('input[name=image_model]').count() === 1 && await page.locator('input[name=media_budget_usd]').count() === 1);

  // 4. Studio: generate a logo.
  await nav('studio');
  await page.waitForSelector('#studio-composer .purpose-chip');
  await page.waitForFunction(() => document.querySelectorAll('#studio-composer select[name=model] option').length > 1, null, { timeout: 15000 });
  check('Live image model catalog populates picker', true, (await page.locator('#studio-composer select[name=model] option').count()) + ' options');
  await page.fill('#studio-composer textarea[name=prompt]', 'A friendly wheat-and-sun mark for Fixture Bakery');
  await page.click('#studio-composer button[type=submit]');
  await page.waitForSelector('.asset-card.image img', { timeout: 30000 });
  const imgOk = await page.locator('.asset-card.image img').first().evaluate(i => i.complete && i.naturalWidth > 0);
  check('Generated logo renders in gallery', imgOk);
  const meta = await page.locator('.asset-card.image .asset-meta').first().textContent();
  check('Asset shows model and real cost', meta.includes('google/gemini-3.1-flash-image') && meta.includes('$0.04'), meta);
  const spend = await page.locator('#studio-spend small').textContent();
  check('Spend meter breaks out provider-reported image cost', spend.includes('images $0.04') && spend.includes('video $0.00'), spend);
  await page.locator('.asset-card.image').first().getByRole('button', { name: 'Use as workspace logo' }).click();
  await page.waitForFunction(() => !document.querySelector('#company-brand-logo').hidden, null, { timeout: 8000 }).catch(() => {});
  check('Logo becomes the workspace logo', !(await page.locator('#company-brand-logo').isHidden()));
  await page.screenshot({ path: OUT + '/02-studio-image.png', fullPage: true });

  // 5. Video render (async job → poll → download).
  await page.click('#studio-tabs button[data-tab=video]');
  await page.waitForSelector('#studio-composer textarea');
  await page.waitForFunction(() => document.querySelector('#studio-composer .form-help')?.textContent.includes('OpenRouter pricing'), null, { timeout: 15000 });
  check('Video composer shows live pricing', true, await page.locator('#studio-composer p.form-help').first().textContent());
  await page.fill('#studio-composer textarea', 'Slow push-in on a basket of warm bread, morning light');
  await page.click('#studio-composer button[type=submit]');
  await page.waitForSelector('.asset-card.job', { timeout: 8000 });
  check('Render job shows while rendering', true);
  await page.click('#studio-filters button[data-filter=all]');
  await page.waitForSelector('.asset-card.video video', { timeout: 60000 });
  check('Finished video appears with player', await page.locator('.asset-card.video video').count() === 1);

  // 6. Engineer builds a landing page via employee tool loop.
  await page.click('#studio-tabs button[data-tab=page]');
  await page.waitForSelector('#studio-composer textarea');
  await page.click('#studio-composer button[type=submit]');
  await page.waitForSelector('#chat-view:not([hidden])', { timeout: 8000 });
  await page.waitForFunction(() => [...document.querySelectorAll('#chat-messages')].some(n => /Fixture deliverable/.test(n.textContent)), null, { timeout: 30000 });
  check('Engineer completed the page brief in chat', true);
  await nav('studio');
  await page.waitForSelector('.funnel-group .asset-card.page', { timeout: 10000 });
  check('Landing page saved under its funnel', (await page.locator('.funnel-group h3').first().textContent()) === 'Office catering');
  await page.locator('.funnel-group .asset-card.page').getByRole('button', { name: 'Preview' }).click();
  const frame = page.frameLocator('#os-dialog iframe');
  await frame.locator('h1').waitFor({ timeout: 10000 });
  check('Sandboxed preview renders the page', (await frame.locator('h1').textContent()).includes('Fresh bread'));
  const inlined = await frame.locator('img').getAttribute('src');
  check('Brand image inlined into page', inlined && inlined.startsWith('data:image/png;base64,'));
  await page.waitForTimeout(800);
  const leaked = await frame.locator('body').evaluate(b => document.title === 'LEAKED' ? 'LEAKED' : (b.dataset.sandbox || 'pending'));
  check('Generated page cannot read workspace state (sandbox)', leaked === 'blocked', leaked);
  await page.screenshot({ path: OUT + '/03-page-preview.png' });
  await page.click('#os-dialog button[aria-label="Mobile"], #os-dialog .preview-bar button:nth-child(2)');
  await page.waitForTimeout(400);
  await page.screenshot({ path: OUT + '/04-page-mobile.png' });
  await page.click('#close-dialog');

  // 7. Designer via chat generates an asset; task shows cost and asset.
  await nav('chat');
  await page.selectOption('#chat-owner', { label: /Iris/ }).catch(async () => { const v = await page.locator('#chat-owner option', { hasText: 'Iris' }).getAttribute('value'); await page.selectOption('#chat-owner', v); });
  await page.fill('#message', 'Make our launch post');
  await page.click('#send-message');
  await page.waitForFunction(() => /Fixture deliverable/.test(document.querySelector('#chat-messages').textContent), null, { timeout: 30000 });
  await nav('work');
  await page.locator('#work-list .work-row', { hasText: 'Make our launch post' }).getByRole('button').first().click();
  await page.waitForSelector('#os-dialog .task-assets .asset-card', { timeout: 8000 });
  const cost = await page.locator('#os-dialog .review-meta', { hasText: 'Provider-reported cost' }).textContent();
  check('Task detail shows created asset and cost', cost.includes('$'), cost);
  await page.click('#close-dialog');

  // 8. Export zip.
  await nav('studio');
  const href = await page.locator('#studio-export').getAttribute('href');
  const zip = await page.request.get(BASE + href);
  check('Export all returns a zip', zip.ok() && (await zip.body()).slice(0, 2).toString() === 'PK', zip.headers()['content-type']);

  // 9. Workflow template availability.
  await nav('flows');
  await page.click('#new-workflow');
  const templates = await page.locator('#os-dialog select[name=template_id] option').allTextContents();
  check('Brand launch kit workflow is available with this crew', templates.includes('Brand launch kit'), templates.join(' | '));
  await page.click('#close-dialog');

  // 10. Mobile layout.
  await page.setViewportSize({ width: 390, height: 844 });
  await nav('studio');
  await page.waitForTimeout(400);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check('Studio has no horizontal overflow at 390px', overflow <= 1, 'overflow ' + overflow + 'px');
  await page.screenshot({ path: OUT + '/05-studio-mobile.png', fullPage: true });

  check('No uncaught page or console errors', errors.length === 0, errors.slice(0, 5).join(' | '));
  await browser.close();
  const failed = results.filter(r => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch(e => { console.error('E2E crashed:', e); process.exit(2); });
