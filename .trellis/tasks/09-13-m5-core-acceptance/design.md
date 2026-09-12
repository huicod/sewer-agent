# T15-A 验收设计

隔离的浏览器验收夹具不是产品部署器：沿用真实app factory和所有相关workflow/activity/repository，只在模型适配层注入受控替身。API和CPU/GPU职责worker运行于测试进程，GPU职责仍串行但不加载真实模型；PostgreSQL/Temporal使用现有本机服务和唯一schema/queue。不向生产API添加测试路由。

## 测试服务与固定边界

`backend/tests/browser_server.py` 显式 `SEWER_RUN_BROWSER_TEST=1` 才允许启动，监听127.0.0.1:18080。`SEWER_BROWSER_OUTPUT` 指向本轮尚不存在的目录；启动时生成fixtures/image.png与fixtures/video.mp4（64×64，短视频约1.2s/6帧），资产放assets/；不含任何真实用户媒体。
Settings仅从现有配置取得本机数据库/Temporal连接，在内存固定测试model pins、queue及asset_root；不打印密钥或完整Settings。受控检测输出一个AJ中置信度框；受控VLMabstain，使审核页面必达；受控parser支持inspect/ask/report/compare，不能执行任意文本工具。HTTP响应全部来自产品路由。
启动API前初始化独立schema并启动workflows：ConversationWorkflow/ImageInspectionWorkflow/VideoInspectionWorkflow及所需审核Activities；检测器元信息与run pins匹配。退出终止仅本轮仍运行的workflow，停止自身worker，删除仅自己创建的schema，保留截图/fixture/有用结果。失败也须清理，不能吞掉失败使进程假成功。

## 浏览器入口

根目录frontend增加固定版本@playwright/test开发依赖与仅Chromium配置，不增加业务依赖。Playwright管理两个独立本机测试服务：上述API、测试专用Vite配置（18081，/api→18080），reuseExistingServer=false，不覆盖已有服务。业务vite.config.ts继续默认8000；单元测试限制src避免误收集e2e。
`frontend/e2e/core.spec.ts` 使用页面真实文件上传和表单操作，至少检查图片待审→刷新保留→提交审核→完成→报告，短视频待审→审核→事件时间轴/报告，第二图片与第一图片保存比较。每步按状态/DOM条件等待，不用固定sleep；无page.route假接口。保存关键页面截图至测试输出目录。
脚本输出目录由Playwright配置一次生成并传入两服务，不因不同进程时钟产生路径分歧。失败保留trace，成功可保留截图/JSON必要证据。测试无付费API/GPU，不声称准确率、长视频吞吐、收费账单或真实模型UI集成已全面验收。

## 所有权与范围

后端夹具agent只写backend/tests/browser_server.py（必要时同目录browser_fixtures.py和其轻量测试），不改production。主agent负责frontend/e2e与配置/包锁、浏览器运行、故障测试和已证实生产问题修复；UI设计不重做。独立审查只读一次；jj仅主agent可写。T14-B和T16不在本轮实现范围。
