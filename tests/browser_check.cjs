const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const { pathToFileURL } = require('url');

async function main() {
  const root = process.argv[2];
  const modulePath = process.env.PLAYWRIGHT_MODULE;
  if (!modulePath) throw new Error('PLAYWRIGHT_MODULE is required');
  const { chromium } = require(modulePath);
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'chromium', headless: true});
  let checks = 0;
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.name));
    await page.route('http://**', route => route.abort());
    await page.route('https://**', route => route.abort());
    await page.setViewportSize({width: 1440, height: 1000});
    await page.goto(pathToFileURL(path.join(root, 'report.html')).href);
    assert.equal(await page.title(), '小时渠道诊断'); checks++;
    assert.equal(await page.locator('#control-start').count(), 1); checks++;
    assert.equal(await page.locator('#control-enable-start').count(), 1); checks++;
    assert.equal(await page.locator('#control-run-once').count(), 1); checks++;
    assert.equal(await page.locator('#control-stop').count(), 1); checks++;
    assert.equal(await page.locator('#details tbody tr').count(), 22); checks++;
    assert.equal(await page.locator('#overview tbody tr').count(), 4); checks++;
    assert.equal(await page.locator('#overview tbody tr td:nth-child(3)').first().innerText(), '0.4'); checks++;
    assert.ok(await page.locator('#details tbody').innerText().then(text => text.includes('0.4×'))); checks++;
    assert.equal(await page.locator('#details tbody tr').filter({hasText:'verified'}).count(), 10); checks++;
    assert.equal(await page.locator('#details tbody tr').filter({hasText:'不适用 / 未返回'}).count(), 6); checks++;
    assert.ok(await page.locator('svg circle').count() > 0); checks++;
    const detail = page.locator('details').first();
    await detail.locator('summary').click();
    assert.equal(await detail.getAttribute('open'), null); checks++;
    await detail.locator('summary').click();
    await page.screenshot({path:path.join(root,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),true); checks++;
    assert.ok(await page.locator('.scroll').evaluateAll(nodes => nodes.some(node => node.scrollWidth > node.clientWidth))); checks++;
    await page.screenshot({path:path.join(root,'mobile.png'),fullPage:true});
    assert.equal(errors.length,0); checks++;
    const statusPage = await browser.newPage();
    await statusPage.addInitScript(() => {
      window.__controlTest = {
        state: 'running', phase: 'checking', enabled_channels: 2, total_channels: 2,
        last_run: {label:'执行中', executed_requests:3, planned_requests:110,
                   succeeded_requests:2, started_at:'2026-09-23T00:00:00+00:00'}
      };
      window.fetch = async () => ({ok:true, json:async () => window.__controlTest});
    });
    await statusPage.goto(pathToFileURL(path.join(root, 'report.html')).href);
    await statusPage.locator('#control-state').getByText('检测中').waitFor(); checks++;
    assert.ok((await statusPage.locator('#control-last-run').innerText()).includes('3/110 项')); checks++;
    assert.equal(await statusPage.locator('#control-run-once').isDisabled(), true); checks++;
    await statusPage.evaluate(() => {
      window.__controlTest = {...window.__controlTest, state:'failed', phase:'failed', last_exit_code:7};
      document.getElementById('control-refresh').click();
    });
    await statusPage.locator('#control-state').getByText('运行异常').waitFor(); checks++;
    assert.ok((await statusPage.locator('#control-message').innerText()).includes('退出码 7')); checks++;
    await statusPage.evaluate(() => {
      window.__controlTest = {...window.__controlTest, last_stop_reason:'control_token_missing'};
      document.getElementById('control-refresh').click();
    });
    await statusPage.locator('#control-message').getByText('控制令牌未配置', {exact:false}).waitFor(); checks++;
    await statusPage.close();
    fs.writeFileSync(path.join(root,'browser.json'),JSON.stringify({status:'passed',checks},null,2));
  } finally {await browser.close();}
}
main().catch(error => {process.stderr.write(`${error.name}: ${error.message}\n`);process.exitCode=1;});
