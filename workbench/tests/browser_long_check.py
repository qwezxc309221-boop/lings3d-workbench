"""通过 Chrome CDP 检查界面；需要已启动的工作台和 9223 调试端口。"""
import asyncio
import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen
import websockets

async def main():
    request=Request('http://127.0.0.1:9223/json/new?http://localhost:8080',method='PUT')
    target=json.load(urlopen(request))
    counter=0
    async def call(ws,method,params=None):
        nonlocal counter
        counter+=1;identifier=counter
        await ws.send(json.dumps(dict(id=identifier,method=method,params=params or {})))
        while True:
            reply=json.loads(await ws.recv())
            if reply.get('id')==identifier:
                if 'error' in reply:raise RuntimeError(reply)
                return reply.get('result',{})
    async def evaluate(ws,expression):
        reply=await call(ws,'Runtime.evaluate',dict(expression=expression,returnByValue=True,awaitPromise=True))
        if 'exceptionDetails' in reply:raise RuntimeError(reply)
        return reply.get('result',{}).get('value')
    async with websockets.connect(target['webSocketDebuggerUrl'],max_size=20000000) as page:
        await call(page,'Runtime.enable');await call(page,'Page.enable')
        await call(page,'Emulation.setDeviceMetricsOverride',dict(width=1520,height=1300,deviceScaleFactor=1,mobile=False))
        await call(page,'Page.bringToFront')
        await asyncio.sleep(3)
        info=await evaluate(page,'({mode:document.querySelector("#reconstruction-mode").value,view:!document.querySelector("#viewer").hidden,button:document.querySelector("#view").disabled,href:document.querySelector("#download-npz").href})')
        print('page',info);assert info['mode']=='stereo' and info['view'] and not info['button']
        assert 'mode=stereo' in info['href']
        targets=json.load(urlopen('http://127.0.0.1:9223/json'))
        iframe=next(t for t in targets if t['type']=='iframe' and ':8091' in t['url'])
        async with websockets.connect(iframe['webSocketDebuggerUrl'],max_size=20000000) as view:
            await call(view,'Runtime.enable')
            text=await evaluate(view,'document.body.innerText')
            print('viewer',text[:900]);assert '双目视差' in text and '双目支持区域' in text
            assert await evaluate(view,'document.querySelectorAll("canvas").length')>0
            for kind in ('PLY','GLB'):
                await evaluate(view,'Array.from(document.querySelectorAll("button")).find(x=>x.textContent.includes('+json.dumps('导出当前过滤点云 '+kind)+'))?.click()')
                await asyncio.sleep(.5)
        s=json.load(urlopen('http://127.0.0.1:8080/api/state'));v=s['view']
        for name in ('fusion_report.json','depth_comparison.png','pointcloud.ply','pointcloud.glb'):
            url=f"http://127.0.0.1:8080/api/download/{v['scan_id']}/{v['segment']}/{name}?mode=stereo"
            data=urlopen(url).read();assert len(data)>100;print('download',name,len(data))
        # 长序列逐帧回放仅加载目标帧所在块。
        targets=json.load(urlopen('http://127.0.0.1:9223/json'))
        iframe=next(t for t in targets if t['type']=='iframe' and ':8091' in t['url'])
        async with websockets.connect(iframe['webSocketDebuggerUrl'],max_size=20000000) as view:
            await call(view,'Runtime.enable')
            assert '326 帧' in await evaluate(view,'document.body.innerText')
            await evaluate(view,'Array.from(document.querySelectorAll("input[role=combobox]")).find(x=>x.value==="全部帧").click()')
            await asyncio.sleep(.3)
            await evaluate(view,'Array.from(document.querySelectorAll("[role=option]")).find(x=>x.textContent==="当前帧").click()')
            await asyncio.sleep(1)
            assert '当前帧' in await evaluate(view,'Array.from(document.querySelectorAll("input[role=combobox]")).map(x=>x.value).join()')
            await evaluate(view,'Array.from(document.querySelectorAll("button")).find(x=>x.textContent.includes("导出当前过滤点云 PLY")).click()')
            await asyncio.sleep(.5)
            await evaluate(view,'Array.from(document.querySelectorAll("input[role=combobox]")).find(x=>x.value==="当前帧").click()')
            await asyncio.sleep(.3)
            await evaluate(view,'Array.from(document.querySelectorAll("[role=option]")).find(x=>x.textContent==="全部帧").click()')
            await asyncio.sleep(1)
        await evaluate(page,'document.querySelector("#viewer").scrollIntoView({block:"center"})')
        await asyncio.sleep(2)
        shot=await call(page,'Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
        Path(__file__).resolve().parents[1].joinpath('runtime/long_audit/long_workbench.png').write_bytes(base64.b64decode(shot['data']))
        print('PASS: long UI, 326 frames, WebGL, lazy current frame and exports')
asyncio.run(main())
