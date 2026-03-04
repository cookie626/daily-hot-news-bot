import os
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from dateutil import tz
from openai import OpenAI

# 环境变量
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL")

# DeepSeek 客户端（OpenAI 兼容）
client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

# 36氪 主站 RSS
KR36_RSS_URL = "https://36kr.com/feed"


def get_yesterday_range_cn():
    """
    计算北京时间的“昨天 00:00 ~ 23:59:59”
    返回 (start_dt, end_dt, 日期字符串)
    """
    tz_cn = timezone(timedelta(hours=8))
    now = datetime.now(tz_cn)
    yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
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
        # published_parsed 是 struct_time，需要转成 datetime
        published_parsed = getattr(e, "published_parsed", None) or e.get("published_parsed")
        if not published_parsed:
            continue

        # 先当成 UTC，再转北京时间（多数 RSS 如此；有误差也问题不大）
        dt_utc = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        dt_cn = dt_utc.astimezone(timezone(timedelta(hours=8)))

        if start_dt <= dt_cn <= end_dt:
            result.append((e, dt_cn))
    return result


def build_llm_prompt_36kr(date_str, entries_with_time):
    """
    基于 36氪 昨日文章，构造给大模型的提示词
    """
    lines = []
    lines.append(f"你是一名专业的中文科技与商业新闻编辑，请根据下面 36氪 在 {date_str} 发布的文章，生成一份「昨日热点摘要」，用于发到飞书群。")
    lines.append("")
    lines.append("要求：")
    lines.append("1. 从中筛选出对科技、商业、创业、消费、宏观趋势等真正有价值的热点，合并相似主题。")
    lines.append("2. 归纳成几个板块（例如：科技 / 创业融资 / 行业趋势 / 政策监管 / 消费与生活 等），不必强行每个板块都有。")
    lines.append("3. 每条热点：先一句话总结，再补充 1~3 句关键信息。")
    lines.append("4. 在适当位置点名一句『据 36氪报道』，但不要每条都重复。")
    lines.append("5. 最后给出一个 2~4 句的总览小结，概括昨日整体风向。")
    lines.append("6. 输出请直接用 Markdown 风格的分级标题和列表，适合在飞书中直接阅读。")
    lines.append("")
    lines.append("下面是昨日的 36氪 文章列表：")
    lines.append("")

    for idx, (e, dt_cn) in enumerate(entries_with_time, 1):
        title = getattr(e, "title", "") or e.get("title", "")
        summary = getattr(e, "summary", "") or e.get("summary", "")
        link = getattr(e, "link", "") or e.get("link", "")
        published_str = dt_cn.strftime("%Y-%m-%d %H:%M")

        lines.append(f"【文章 {idx}】")
        lines.append(f"标题：{title}")
        lines.append(f"时间（北京）：{published_str}")
        if summary:
            lines.append(f"摘要：{summary}")
        if link:
            lines.append(f"链接：{link}")
        lines.append("")

    return "\n".join(lines)


def summarize_with_deepseek(prompt):
    """
    调用 DeepSeek 生成摘要
    """
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "你是一名严谨的中文科技与商业新闻编辑，擅长从 36氪 等媒体中提炼有价值的热点。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.6,
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
    if not DEEPSEEK_API_KEY or not FEISHU_WEBHOOK_URL:
        raise RuntimeError("请确保 DEEPSEEK_API_KEY、FEISHU_WEBHOOK_URL 已配置为环境变量。")

    start_dt, end_dt, date_str = get_yesterday_range_cn()

    # 1）拉取 36氪 RSS
    entries = fetch_36kr_rss()

    # 2）过滤出昨天的文章
    entries_yesterday = filter_entries_by_yesterday(entries, start_dt, end_dt)

    if not entries_yesterday:
        text = f"【昨日热点播报】{date_str}\n\n昨日在 36氪 上未检测到新的文章（或 RSS 抓取异常），请稍后再试或手动查看。"
        send_to_feishu(text)
        print("No entries for yesterday, sent notice to Feishu.")
        return

    # 3）构造给大模型的提示词
    prompt = build_llm_prompt_36kr(date_str, entries_yesterday)

    # 4）让 DeepSeek 总结
    summary = summarize_with_deepseek(prompt)

    # 5）发到飞书
    final_text = f"【36氪 · 昨日热点播报】{date_str}\n\n" + summary
    resp = send_to_feishu(final_text)
    print("Feishu response:", resp)


if __name__ == "__main__":
    main()
