const {chromium}=require('/Users/mima0000/.npm/_npx/705bc6b22212b352/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const url='file:///Users/mima0000/Documents/学习-001/do-project/dontforget/design-demo/index.html';
const report={checks:[],errors:[]};
(async()=>{
  const browser=await chromium.launch({executablePath:'/Users/mima0000/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell'});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:960}});
    page.on('pageerror',e=>report.errors.push(e.message));
    const check=async(name,fn)=>{await fn();report.checks.push(name);console.log('PASS',name);};
    await page.goto(url);
    await check('工作空间与输入区可用，切换栏和插画已移除',async()=>{
      assert.equal(await page.locator('[data-workspace]').count(),2);
      assert.equal(await page.locator('#workspaceSelect').inputValue(),'project');
      assert.equal(await page.locator('[data-workspace-group="project"] [data-session]').count(),4);
      assert.equal(await page.locator('[data-workspace-group="life"] [data-session]').count(),1);
      assert.equal(await page.locator('.composer-mascot').count(),0);
      assert.equal(await page.locator('.intent-tabs, [data-mode]').count(),0);
    });
    await check('无记忆切换栏，工作空间选择保留草稿',async()=>{
      await page.locator('#newInput').fill('保留这段草稿');
      assert.equal(await page.locator('[data-mode]').count(),0);
      assert.equal(await page.locator('#newInput').inputValue(),'保留这段草稿');
      assert.match(await page.locator('.quick-actions').innerText(),/找回/);
      await page.locator('#workspaceSelect').selectOption('life');
      assert.equal(await page.locator('#newInput').inputValue(),'保留这段草稿');
      assert.equal(await page.locator('#workspaceSelect').inputValue(),'life');
    });
    await check('新会话绑定工作空间，已有会话不能被输入区改归属',async()=>{
      await page.locator('#newInput').fill('空间里的独立新会话');
      await page.locator('#newInput').press('Enter');
      await page.locator('#chatInput').waitFor();
      assert.equal(await page.locator('[data-workspace-group="life"] [data-session="5"]').count(),1);
      assert.equal(await page.locator('#workspaceSelect').isDisabled(),true);
      await page.locator('[data-workspace-group="project"] [data-session="0"]').click();
      assert.equal(await page.locator('#workspaceSelect').inputValue(),'project');
      await page.locator('[data-workspace-group="life"] [data-session="5"]').click();
      assert.match(await page.locator('.thread').innerText(),/空间里的独立新会话/);
    });
    await check('空间空记忆不显示其他项目记忆，进入记忆库仍可查看全部',async()=>{
      await page.locator('[data-rail]').first().click();
      assert.match(await page.locator('#rail').innerText(),/暂无/);
      assert.equal(await page.locator('#rail .railitem').count(),0);
      await page.locator('#rail [data-go="memory"]').click();
      await page.waitForURL('**#/memory');
      await page.locator('.memrow').first().waitFor();
      assert.equal(await page.locator('.memrow').count(),7);
    });
    await check('新增空间名校验、转义、空间切换和空会话状态',async()=>{
      await page.locator('#addWorkspace').click();
      assert.equal(await page.locator('#workspaceDialog').isVisible(),true);
      await page.locator('#workspaceName').fill('   ');
      await page.locator('#workspaceCreate').click();
      assert.match(await page.locator('#workspaceError').innerText(),/名称/);
      const name='<img src=x onerror=alert(1)>';
      await page.locator('#workspaceName').fill(name);
      await page.locator('#workspaceCreate').click();
      assert.equal(await page.locator('#workspaceDialog').isVisible(),false);
      await page.locator('#newInput').waitFor();
      assert.equal(await page.locator('[data-workspace]').count(),3);
      assert.match(await page.locator('#sidebar').innerText(),/<img src=x/);
      assert.equal(await page.locator('#sidebar img[src="x"]').count(),0);
      assert.match(await page.locator('.workspace-empty').last().innerText(),/暂无对话/);
      assert.equal(await page.locator('#workspaceSelect option:checked').textContent(),name);
      assert.equal(await page.locator('#newSend').isDisabled(),true);
      await page.locator('#addWorkspace').click();
      await page.locator('#workspaceName').fill(name);
      await page.locator('#workspaceCreate').click();
      assert.match(await page.locator('#workspaceError').innerText(),/已存在/);
      await page.keyboard.press('Escape');
    });
    await check('空间会话可折叠，打开会话会恢复对应空间',async()=>{
      await page.locator('[data-workspace="project"]').click();
      await page.locator('[data-workspace-toggle="project"]').click();
      assert.equal(await page.locator('[data-workspace-group="project"] [data-session="0"]').isVisible(),false);
      await page.locator('[data-workspace-toggle="project"]').click();
      await page.locator('[data-workspace-group="project"] [data-session="0"]').click();
      assert.equal(await page.locator('#workspaceSelect').inputValue(),'project');
    });
    await check('窄屏选择或创建工作空间后关闭侧栏遮罩',async()=>{
      await page.setViewportSize({width:375,height:812});
      await page.goto(url+'#/new');
      await page.locator('[data-sidebar]').click();
      await page.locator('[data-workspace="life"]').click();
      assert.equal(await page.locator('#sidebar').isVisible(),false);
      assert.equal(await page.locator('#workspaceSelect').inputValue(),'life');
      await page.locator('[data-sidebar]').click();
      await page.locator('#addWorkspace').click();
      await page.locator('#workspaceName').fill('移动空间');
      await page.locator('#workspaceCreate').click();
      assert.equal(await page.locator('#sidebar').isVisible(),false);
      assert.equal(await page.locator('#workspaceSelect option:checked').textContent(),'移动空间');
    });
    await page.goto(url+'#/new');await page.reload();
    for(const width of [1440,1024,768,390,320]){
      await page.setViewportSize({width,height:900});
      await check(`${width}px 首页、聊天与空间选择无溢出`,async()=>{
        for(const route of ['new','chat']){
          await page.goto(url+'#/'+route);
          const over=await page.evaluate(()=>({document:document.documentElement.scrollWidth>innerWidth,main:document.querySelector('#main').scrollWidth>document.querySelector('#main').clientWidth+2}));
          assert.deepEqual(over,{document:false,main:false});
          assert.equal(await page.locator('.composer-mascot, .intent-tabs').count(),0);
        }
      });
    }
    await page.setViewportSize({width:1440,height:960});
    await page.goto(url+'#/new');
    await page.screenshot({path:path.join(__dirname,'home.png')});
    await page.goto(url+'#/chat');
    await page.screenshot({path:path.join(__dirname,'chat.png')});
    await page.locator('#themeBtn').click();
    await page.goto(url+'#/new');
    await page.screenshot({path:path.join(__dirname,'dark.png')});
    await page.locator('#themeBtn').click();
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(__dirname,'mobile.png')});
    assert.deepEqual(report.errors,[]);report.success=true;
  }finally{
    fs.writeFileSync(path.join(__dirname,'report.json'),JSON.stringify(report,null,2));
    await browser.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
