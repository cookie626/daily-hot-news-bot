import os
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from dateutil import tz
from openai import OpenAI

# 环境变量（豆包 + 飞书）
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY")
DOUBAO_MODEL_ID = os.getenv("DOUBAO_MODEL_ID")  # 形如 ep-xxxxxxxxxxxx
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL")

# 豆包（Doubao）客户端：使用 OpenAI 兼容接口
client = OpenAI(
    api_key=DOUBAO_API_KEY,
    base_url="https://ark.cn-beijing.volces.com/api/v3"
)

# 36氪 主站 RSS
KR36_RSS_URL = "https://36kr.com/feed"

# 英文 AI / 技术圈 RSS 源（论文 + 技术社区热门）
AI_RSS_SOURCES = {
    # 学术 / 论文：AI 相关的 arXiv 分类
    "arxiv_cs_ai": "https://export.arxiv.org/rss/cs.AI",
    "arxiv_cs_lg": "https://export.arxiv.org/rss/cs.LG",
    "arxiv_cs_cl": "https://export.arxiv.org/rss/cs.CL",
    "arxiv_cs_cv": "https://export.arxiv.org/rss/cs.CV",

    # Hacker News 热门（首页，已经按热度排序，经常包含 AI 重大新闻）
    "hn_frontpage": "https://hnrss.org/frontpage",

    # Hacker News 最新中包含 AI 关键词的帖子
    "hn_ai_newest": "https://hnrss.org/newest?q=ai+OR+%22artificial+intelligence%22+OR+%22large+language+model%22",

    # Reddit: 机器学习社区，按最近一天的热门排序
    "reddit_MachineLearning_top_day": "https://www.reddit.com/r/MachineLearning/top/.rss?t=day",

    # Reddit: 本地大模型 / 开源 LLM 社区，最近一天热门
    "reddit_LocalLLaMA_top_day": "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day",
}

# 中文社区热门 RSS 源（通过 RSSHub 等聚合服务）
# 注意：下面这些 URL 需要对应服务可访问，如果某个源暂时不可用，不会影响整体运行，只是少一些条目。
CN_COMMUNITY_RSS_SOURCES = {
    # B站：全站热门视频（示例路由，来自 RSSHub）
    # 文档：https://docs.rsshub.app/zh/social-media.html#bi-li-bi-li
    "bilibili_hot": "https://rsshub.app/bilibili/popular/all",

    # 微信：热门文章（示例路由，具体以 RSSHub 文档为准）
    # 有些部署使用 /wechat/mp/hot，具体可能略有差异
    "wechat_hot": "https://rsshub.app/wechat/mp/hot",

    # 小红书：热门笔记（示例路由）
    # 文档：https://docs.rsshub.app/zh/social-media.html#xiao-hong-shu
    "xiaohongshu_hot": "https://rsshub.app/xiaohongshu/hot",

    # 知乎：热榜
    # 文档：https://docs.rsshub.app/zh/social-media.html#zhi-hu
    "zhihu_hot": "https://rsshub.app/zhihu/hotlist",
}


def get_yesterday_range_cn():
    """
    计算北京时间的“昨天 00:00 ~ 23:59:59”
    返回 (start_dt, end_dt, 日期字符串)
    """
    tz_cn = timezone(timedelta(hours=8))
    now = datetime.now(tz_cn)
    yesterday = (now - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = yesterday
    end = yesterday.replace(hour=23, minute=59, second=59)
    date_str = yesterday.strftime("%Y-%m-%d")
    return start, end, date_str


def fetch_36kr_rss():
    """
    拉取 36氪 RSS 全部条目
    """
    feed = feedparser.parse(KR36_RSS_URL)
    return feed.entries


def filter_entries_by_yesterday(entries, start_dt, end_dt):
    """
    只保留昨天（北京时间）发布的文章
    """
    result = []
    for e in entries:
        published_parsed = getattr(e, "published_parsed", None) or e.get("published_parsed")
        if not published_parsed:
            continue

        # 通常 RSS 时间是 UTC，这里先当 UTC，再转北京时间
        dt_utc = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        dt_cn = dt_utc.astimezone(timezone(timedelta(hours=8)))

        if start_dt <= dt_cn <= end_dt:
            result.append((e, dt_cn))
    return result


def fetch_ai_feeds():
    """
    拉取多路 AI 相关 & 中英文社区热门 RSS，合并成一个列表，带来源标签
    返回: List[dict]，每个 dict:
      - source: 源名称，如 'arxiv_cs_ai' / 'bilibili_hot'
      - title: 标题
      - summary: 摘要
      - link: 链接
      - published: datetime（北京时间）
    只保留最近 2 天内的条目，并每个源最多取 30 条，避免过长。
    """
    items = []
    tz_cn = timezone(timedelta(hours=8))
    now = datetime.now(tz_cn)
    earliest = now - timedelta(days=2)

    # 把英文技术源和中文社区源合并
    all_sources = {}
    all_sources.update(AI_RSS_SOURCES)
    all_sources.update(CN_COMMUNITY_RSS_SOURCES)

    for source_name, url in all_sources.items():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"Error parsing AI feed {source_name}: {e}")
            continue

        count = 0
        for entry in feed.entries:
            if count >= 30:
                break

            title = getattr(entry, "title", "") or entry.get("title", "")
            link = getattr(entry, "link", "") or entry.get("link", "")
            summary = getattr(entry, "summary", "") or entry.get("summary", "")

            if not title or not link:
                continue

            published_parsed = getattr(entry, "published_parsed", None) or entry.get("published_parsed")
            if not published_parsed:
                # 没有时间就当最近
                dt_cn = now
            else:
                # 大多数 RSS 是 UTC，这里当 UTC 处理
                dt_utc = datetime(*published_parsed[:6], tzinfo=timezone.utc)
                dt_cn = dt_utc.astimezone(tz_cn)

            # 只要最近 2 天的
            if dt_cn < earliest:
                continue

            items.append({
                "source": source_name,
                "title": title,
                "summary": summary,
                "link": link,
                "published": dt_cn,
            })
            count += 1

    # 按时间倒序排序，让最近的在前
    items.sort(key=lambda x: x["published"], reverse=True)
    return items


def build_llm_prompt_with_ai(date_str, entries_with_time, ai_items):
    """
    综合 36氪 + 多路 AI / 社区热门数据源，构造给豆包的大 Prompt
    """
    lines = []

    # 整体目标
    lines.append(f"你是一名资深中文科技与商业新闻编辑，需要为飞书群制作一份「{date_str} 的每日热点摘要」，包含：")
    lines.append("1）综合科技/商业资讯（主要来自 36氪）")
    lines.append("2）AI 专栏：最新最热点的 AI 相关内容（论文、技术报道、社区高热度帖子等）")
    lines.append("")
    lines.append("读者是忙碌的职场人，希望 2~3 分钟把握昨天的重要动态，尤其是 AI 相关的大事。")
    lines.append("请尽量少而精，宁可少、不要碎。")
    lines.append("")

    # 综合资讯部分要求（分级 + 聚焦）
    lines.append("【综合资讯部分要求】")
    lines.append("1. 从 36氪 文章中筛选对「科技 / 商业 / 创业 / 投融资 / 行业趋势 / 政策监管」有实质信息价值的事件。")
    lines.append("2. 宁可少、不要碎：")
    lines.append("   - 优先：政策与监管变化、大厂或头部公司动态、重大融资与并购、新产品/新技术里程碑、影响一个行业走向的趋势。")
    lines.append("   - 弱化：单一小公司软文、纯宣传稿件、对宏观趋势无实际影响的小新闻。")
    lines.append("3. 全文综合资讯热点总数控制在 8~12 条之间，最多不超过 15 条。")
    lines.append("4. 对高度重复/同一事件的多篇报道要合并成一条。")
    lines.append("5. 请按板块分组输出，例如（并不要求全部都用）：")
    lines.append("   - 科技与产品")
    lines.append("   - 创业与投融资")
    lines.append("   - 行业与商业趋势")
    lines.append("   - 政策与监管")
    lines.append("   - 消费与生活方式")
    lines.append("6. 每条热点请标注影响力等级：【高】【中】【低】 三档之一，并总体按【高】在前，【低】在后排序。")
    lines.append("   - 【高】：对一个行业/赛道/宏观环境有显著影响，大厂/监管层/资本市场高度相关。")
    lines.append("   - 【中】：对某一细分领域、部分公司有实质影响，值得相关从业者了解。")
    lines.append("   - 【低】：有一定参考价值，但对整体格局影响有限，可作为补充阅读。")
    lines.append("")

    # AI 专栏部分要求
    lines.append("【AI 专栏部分要求】")
    lines.append("1. AI 相关原始素材中，source 字段可能包含：")
    lines.append("   - arxiv_xxx：代表 arXiv 上的学术论文；")
    lines.append("   - hn_xxx / reddit_xxx：代表英文技术社区的热门讨论；")
    lines.append("   - bilibili_xxx / wechat_xxx / xiaohongshu_xxx / zhihu_xxx：代表中文社区的热门内容（B站、公众号、小红书、知乎等）。")
    lines.append("2. 只保留真正重要或有代表性的 AI 相关内容（论文、技术突破、产品发布、政策、社区大热讨论等）。")
    lines.append("3. 请从以下角度判断“最热点”：")
    lines.append("   - 是否来自权威会议/知名机构/头部公司；")
    lines.append("   - 是否在社区中有显著讨论热度，例如：来自 B站/公众号/小红书/知乎 等平台的高播放、高点赞、高评论内容（即使 RSS 中没有具体数字，也请根据标题、描述和来源进行合理判断）；")
    lines.append("   - 是否对行业方向/大模型能力/产品形态产生显著影响。")
    lines.append("4. AI 专栏的条目总数控制在 5~10 条之间，优先保证质量。")
    lines.append("5. 每条 AI 热点请包含：")
    lines.append("   - 【高/中/低】热度等级 + 一句话标题；")
    lines.append("   - 1~3 句核心说明（做了什么 / 关键创新点 / 可能影响）；")
    lines.append("   - 至少给出 1 个可直接访问的原文链接（论文 / 新闻 / 帖子），链接请用 Markdown 形式，例如：[标题](链接)。")
    lines.append("6. 如果多条来源指向同一事件，请合并为一条，并在说明中点出“多处社区/媒体讨论”。")
    lines.append("7. 明显与 AI / 大模型 / AIGC / 智能体 无关的热门内容，即使热度很高也可以忽略。")
    lines.append("")

    # 整体输出结构
    lines.append("【整体输出结构（请严格按此顺序输出）】")
    lines.append("1. 顶部：2~4 句的【昨天整体风向小结】。")
    lines.append("2. 中部第一部分标题：`## 综合资讯`，按板块 +【高/中/低】分级列出。")
    lines.append("3. 中部第二部分标题：`## AI 专栏`，单独列出 AI 相关热点，同样按【高/中/低】分级。")
    lines.append("4. 底部：给读者 1~2 条“后续可关注方向”的建议，尤其是 AI 领域。")
    lines.append("")

    # 附上综合资讯原始素材（36氪）
    lines.append("【以下是综合资讯原始素材（36氪 昨日文章，按北京时间）】")
    lines.append("")
    for idx, (e, dt_cn) in enumerate(entries_with_time, 1):
        title = getattr(e, "title", "") or e.get("title", "")
        summary = getattr(e, "summary", "") or e.get("summary", "")
        link = getattr(e, "link", "") or e.get("link", "")
        published_str = dt_cn.strftime("%Y-%m-%d %H:%M")

        lines.append(f"【36氪文章 {idx}】")
        lines.append(f"标题：{title}")
        lines.append(f"时间（北京）：{published_str}")
        if summary:
            lines.append(f"摘要：{summary}")
        if link:
            lines.append(f"链接：{link}")
        lines.append("")

    # 附上 AI / 社区原始素材
    lines.append("")
    lines.append("【以下是 AI 相关原始素材（来自 arXiv / Hacker News / Reddit / B站 / 微信 / 小红书 / 知乎 等多路 RSS，时间为北京时间）】")
    lines.append("")
    for idx, item in enumerate(ai_items, 1):
        lines.append(f"【AI条目 {idx}】")
        lines.append(f"来源：{item.get('source', '')}")
        lines.append(f"标题：{item.get('title', '')}")
        if item.get("summary"):
            lines.append(f"摘要：{item['summary']}")
        if item.get("link"):
            lines.append(f"链接：{item['link']}")
        if item.get("published"):
            lines.append(f"时间：{item['published'].strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

    return "\n".join(lines)


def summarize_with_doubao(prompt):
    """
    调用豆包（Doubao）生成摘要
    DOUBAO_MODEL_ID 应为 Ark 控制台中接入点 ID（ep-xxxx 形式）
    """
    if not DOUBAO_MODEL_ID:
        raise RuntimeError("请设置环境变量 DOUBAO_MODEL_ID（豆包推理接入点 ID，形如 ep-xxxx）。")

    resp = client.chat.completions.create(
        model=DOUBAO_MODEL_ID,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一名极其挑剔的中文科技与商业新闻编辑。"
                    "你的目标是：在保证信息准确的前提下，尽量减少无价值或重复的信息，"
                    "只保留真正影响趋势、行业格局或关键参与方决策的事件，并为读者节省时间。"
                    "当 source 显示为 bilibili/wechat/xiaohongshu/zhihu 时，"
                    "请将其视为中文社区热门内容的来源，优先在 AI 专栏中筛选出与 AI / 大模型 / AIGC 相关且热度高的条目。"
                    "请严格按照用户给出的输出结构和分级要求组织内容。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
        max_tokens=2000,
    )
    return resp.choices[0].message.content


def send_to_feishu(text):
    """
    用飞书自定义机器人 Webhook 发送文本消息
    """
    headers = {"Content-Type": "application/json"}
    payload = {
        "msg_type": "text",
        "content": {
            "text": text
        }
    }
    resp = requests.post(FEISHU_WEBHOOK_URL, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def main():
    if not DOUBAO_API_KEY or not FEISHU_WEBHOOK_URL:
        raise RuntimeError("请确保 DOUBAO_API_KEY、DOUBAO_MODEL_ID、FEISHU_WEBHOOK_URL 已配置为环境变量。")

    start_dt, end_dt, date_str = get_yesterday_range_cn()

    # 1）综合资讯（36氪）
    entries = fetch_36kr_rss()
    entries_yesterday = filter_entries_by_yesterday(entries, start_dt, end_dt)

    # 2）AI 专栏数据（arXiv + HN + Reddit + 中文社区热榜）
    ai_items = fetch_ai_feeds()

    if not entries_yesterday and not ai_items:
        text = f"【每日热点播报】{date_str}\n\n昨日未检测到新的资讯和 AI 相关条目（或 RSS 抓取异常），请稍后再试或手动查看。"
        send_to_feishu(text)
        print("No entries for yesterday, sent notice to Feishu.")
        return

    # 3）构造给大模型的提示词（包含综合 + AI 专栏）
    prompt = build_llm_prompt_with_ai(date_str, entries_yesterday, ai_items)

    # 4）让豆包生成完整日报
    summary = summarize_with_doubao(prompt)

    # 5）发到飞书
    final_text = f"【每日热点 · {date_str}】\n\n" + summary
    resp = send_to_feishu(final_text)
    print("Feishu response:", resp)


if __name__ == "__main__":
    main()
