"""默认示例页：每个版式一页带占位内容，供 ``init`` 与编辑器新建使用。

拆分自 :mod:`fcmd.apps.pptmake._core` 以控制单文件行数；本模块只在
``new_slide`` 里按需导入，避免与 ``_core`` 形成循环导入。
"""

from __future__ import annotations

from fcmd.apps.pptmake._core import SlideSpec
from fcmd.apps.pptmake._layouts import (
    LAYOUT_AGENDA,
    LAYOUT_AGENDA_PAGES,
    LAYOUT_APPENDIX,
    LAYOUT_BULLETS,
    LAYOUT_CARDS,
    LAYOUT_CHART,
    LAYOUT_CHART_CONCLUSION,
    LAYOUT_CLOSING,
    LAYOUT_CODE,
    LAYOUT_COMPARISON,
    LAYOUT_CONTACT,
    LAYOUT_COVER,
    LAYOUT_IMAGE,
    LAYOUT_METRICS,
    LAYOUT_PHOTO_COVER,
    LAYOUT_PHOTO_SECTION,
    LAYOUT_PROBLEM_SOLUTION,
    LAYOUT_PROCESS,
    LAYOUT_QUOTE,
    LAYOUT_SECTION,
    LAYOUT_TEAM,
    LAYOUT_TESTIMONIAL,
    LAYOUT_TIMELINE,
    LAYOUT_TWO_COLUMN,
)

__all__ = ["sample_slides"]


def sample_slides() -> dict[str, SlideSpec]:
    """返回「版式键 -> 示例页」，键与 :data:`fcmd.apps.pptmake._layouts.LAYOUTS` 一致。"""
    return {
        LAYOUT_COVER: SlideSpec(
            LAYOUT_COVER,
            title="让想法拥有舞台",
            subtitle="品牌演示模板 · 2026",
            note="封面：标题 + 副标题，适合品牌发布会与方案汇报。",
        ),
        LAYOUT_AGENDA: SlideSpec(
            LAYOUT_AGENDA,
            title="本次分享",
            bullets=("项目背景与目标", "现状与挑战", "方案与亮点", "落地计划"),
        ),
        LAYOUT_SECTION: SlideSpec(
            LAYOUT_SECTION,
            title="从问题出发",
            subtitle="01",
            bullets=("先定义真正要解决的问题，再讨论方案。",),
        ),
        LAYOUT_BULLETS: SlideSpec(
            LAYOUT_BULLETS,
            title="三个关键洞察",
            bullets=(
                "用户需要的不是更多功能，而是更短的路径。",
                "设计的价值在于降低理解成本，而不是增加装饰。",
                "可复用的模板，让每一次表达都保持一致的质感。",
            ),
        ),
        LAYOUT_TWO_COLUMN: SlideSpec(
            LAYOUT_TWO_COLUMN,
            title="对照与取舍",
            subtitle="现状|方案",
            bullets=("信息分散，风格不统一", "模板集中管理，随时复用", "手工排版耗时且易错", "版式引擎自动完成排版"),
        ),
        LAYOUT_QUOTE: SlideSpec(
            LAYOUT_QUOTE,
            title="好的设计不是添加，而是删减到刚刚好。",
            subtitle="— 设计原则",
        ),
        LAYOUT_METRICS: SlideSpec(
            LAYOUT_METRICS,
            title="用数据说话",
            bullets=("98%|交付准时率|+3pt", "4.9|客户满意度|+0.2", "120+|覆盖项目"),
        ),
        LAYOUT_TIMELINE: SlideSpec(
            LAYOUT_TIMELINE,
            title="四步交付",
            bullets=("01|调研", "02|设计", "03|实现", "04|交付"),
        ),
        LAYOUT_CLOSING: SlideSpec(
            LAYOUT_CLOSING,
            title="THANK YOU",
            subtitle="期待与你一起，把下一次表达做得更好。",
        ),
        LAYOUT_IMAGE: SlideSpec(
            LAYOUT_IMAGE,
            title="让主视觉先说话",
            subtitle="右侧留出大图区域，文字保持克制。",
            bullets=("主视觉占据右侧约一半版面", "标题与正文左对齐，层级清晰", "图文比例适合品牌与产品发布"),
        ),
        LAYOUT_COMPARISON: SlideSpec(
            LAYOUT_COMPARISON,
            title="两种方案的取舍",
            subtitle="现状|目标",
            bullets=("响应速度|小时级|分钟级", "维护成本|较高|更低", "扩展能力|受限于插件|开放接口"),
        ),
        LAYOUT_CHART: SlideSpec(
            LAYOUT_CHART,
            title="关键指标走势",
            bullets=("Q1|42", "Q2|68", "Q3|54", "Q4|86"),
            unit="%",
        ),
        LAYOUT_CONTACT: SlideSpec(
            LAYOUT_CONTACT,
            title="让我们保持联系",
            subtitle="期待下一次合作，也欢迎随时交流。",
            bullets=("邮箱|hello@example.com", "电话|+86 138 0000 0000", "地址|上海市 · 徐汇区"),
        ),
        LAYOUT_PHOTO_COVER: SlideSpec(
            LAYOUT_PHOTO_COVER,
            title="把复杂，讲清楚",
            subtitle="年度产品发布 · 2026",
            bullets=("视觉先行|一张有情绪的主图，胜过三段解释。",),
        ),
        LAYOUT_PHOTO_SECTION: SlideSpec(
            LAYOUT_PHOTO_SECTION,
            title="从洞察到行动",
            subtitle="02",
            bullets=("这一章回答：为什么现在必须改变。",),
        ),
        LAYOUT_CARDS: SlideSpec(
            LAYOUT_CARDS,
            title="三件事决定体验",
            bullets=(
                "更短的路径|把关键操作前置，减少层级与等待。",
                "更清楚的反馈|每一步都有状态、结果与下一步。",
                "更一致的质感|模板化排版，让每次表达都稳定。",
            ),
        ),
        LAYOUT_PROCESS: SlideSpec(
            LAYOUT_PROCESS,
            title="从想法到上线",
            bullets=(
                "01|梳理|确认目标、范围与约束",
                "02|设计|定稿信息结构与视觉",
                "03|实现|开发、联调与验收",
                "04|上线|灰度发布与复盘",
            ),
        ),
        LAYOUT_PROBLEM_SOLUTION: SlideSpec(
            LAYOUT_PROBLEM_SOLUTION,
            title="问题不在工具，而在流程",
            subtitle="现状|目标",
            bullets=(
                "信息散落在多个文档|统一沉淀到一套模板",
                "每次排版从零开始|版式引擎自动生成",
                "审稿反复对齐格式|输出即规范",
            ),
        ),
        LAYOUT_CHART_CONCLUSION: SlideSpec(
            LAYOUT_CHART_CONCLUSION,
            title="增长来自留存",
            subtitle="留存每提升 1pt，季度收入增加约 2.4%|老客复购贡献了 61% 的增量|下一步聚焦新手引导与召回",
            bullets=("Q1|42", "Q2|68", "Q3|54", "Q4|86"),
            unit="%",
        ),
        LAYOUT_TEAM: SlideSpec(
            LAYOUT_TEAM,
            title="核心团队",
            subtitle="一支把复杂问题做简单的团队",
            bullets=(
                "林岚|产品负责人|十年 B 端产品经验",
                "周野|设计负责人|专注信息设计",
                "陈序|技术负责人|全栈与性能优化",
            ),
        ),
        LAYOUT_TESTIMONIAL: SlideSpec(
            LAYOUT_TESTIMONIAL,
            title="上线两周，团队的周报从半天缩短到二十分钟。",
            subtitle="— 王敏 · 某科技公司运营总监",
        ),
        LAYOUT_CODE: SlideSpec(
            LAYOUT_CODE,
            title="配置只要三行",
            subtitle="YAML",
            bullets=("template: brand-2026", "aspect: 16:9", "output: dist/deck.pptx"),
        ),
        LAYOUT_APPENDIX: SlideSpec(
            LAYOUT_APPENDIX,
            title="附录",
            subtitle="数据口径与参考",
            bullets=("指标定义与计算方式|12", "调研样本与访谈记录|14", "参考案例与致谢|16"),
        ),
        LAYOUT_AGENDA_PAGES: SlideSpec(
            LAYOUT_AGENDA_PAGES,
            title="本次分享",
            subtitle="全程约 25 分钟",
            bullets=("项目背景与目标|03", "现状与挑战|06", "方案与亮点|09", "落地计划|12"),
        ),
    }
