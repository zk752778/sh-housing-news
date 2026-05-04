#!/usr/bin/env python3
"""
上海房产日报 - 每日新闻抓取 + AI摘要 + 邮件推送
运行环境: GitHub Actions，每天北京时间 09:03 自动触发
"""

import os
import smtplib
import feedparser
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import anthropic

# ── 配置 ──────────────────────────────────────────────────────────────────────
SMTP_HOST    = "smtp.163.com"
SMTP_PORT    = 465
EMAIL_FROM   = "zikeliu27@163.com"
EMAIL_TO     = "zikeliu27@163.com"
EMAIL_PASS   = os.environ["EMAIL_PASS"]           # 163邮箱授权码（非登录密码）
CLAUDE_KEY      = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_BASE_URL = os.environ.get("CLAUDE_BASE_URL", "https://api.modelverse.cn")

TZ_BEIJING   = timezone(timedelta(hours=8))

# ── 新闻源（Google News RSS，按关键词搜索）────────────────────────────────────
RSS_FEEDS = [
    "https://news.google.com/rss/search?q=上海+房产+政策&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    "https://news.google.com/rss/search?q=上海+楼���+成交&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    "https://news.google.com/rss/search?q=上海+二手房+新房&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    "https://news.google.com/rss/search?q=上海+限购+公积金&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    "https://news.google.com/rss/search?q=上海+房价+置换&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
]

# ── 1. 抓取前一天新闻 ─────────────────────────────────────────────────────────
def fetch_yesterday_news() -> list[dict]:
    now       = datetime.now(TZ_BEIJING)
    yesterday = (now - timedelta(days=1)).date()
    items     = []
    seen      = set()

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[WARN] 抓取失败: {url} → {e}")
            continue

        for entry in feed.entries:
            pub = entry.get("published_parsed")
            if pub:
                pub_date = datetime(*pub[:6], tzinfo=timezone.utc) \
                           .astimezone(TZ_BEIJING).date()
                if pub_date != yesterday:
                    continue

            title = entry.get("title", "").strip()
            if not title or title in seen:
                continue
            seen.add(title)

            items.append({
                "title":   title,
                "link":    entry.get("link", ""),
                "source":  entry.get("source", {}).get("title", "未知来源"),
                "summary": entry.get("summary", "")[:300],
            })

    return items


# ── 2. Claude API 整理摘要 ────────────────────────────────────────────────────
def summarize_with_claude(items: list[dict], date_str: str) -> str:
    client = anthropic.Anthropic(api_key=CLAUDE_KEY, base_url=CLAUDE_BASE_URL)

    if not items:
        return "<p style='color:#999;'>昨日未检索到相关新闻。</p>"

    news_text = "\n\n".join(
        f"【{i+1}】{item['title']}\n来源：{item['source']}\n链接：{item['link']}\n摘要：{item['summary']}"
        for i, item in enumerate(items)
    )

    prompt = f"""以下是 {date_str} 关于上海房产的新闻，请整理成结构化日报。

要求：
1. 按以下四类分组（无内容的分类直接省略）：
   - 🏛 政策动向（限购、贷款、公积金、税费等官方政策）
   - 📊 市场成交（成交量、成交价格、行情数据）
   - 🏗 楼盘动态（新盘开盘、在售项目、楼盘信息）
   - 💡 其他热点
2. 每条新闻用一句话概括核心内容，保留原始链接
3. 输出 HTML 格式（用 <h3>/<ul>/<li>/<a> 标签），方便邮件展示
4. 语言简洁，中文输出

新闻列表：
{news_text}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


# ── 3. 发送 HTML 邮件 ─────────────────────────────────────────────────────────
def send_email(html_body: str, date_str: str, news_count: int):
    subject = f"【上海房产日报】{date_str} · 共 {news_count} 条热点"

    full_html = f"""
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"></head>
<body style="font-family:'PingFang SC',Arial,sans-serif;max-width:680px;
             margin:auto;padding:20px;color:#333;background:#f9f9f9;">
  <div style="background:#fff;border-radius:8px;padding:24px;
              box-shadow:0 2px 8px rgba(0,0,0,.08);">
    <h2 style="color:#c0392b;margin-top:0;">
      🏠 上海房产日报 &nbsp;<span style="font-size:16px;color:#666;">{date_str}</span>
    </h2>
    <hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
    {html_body}
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0 12px;">
    <p style="color:#aaa;font-size:12px;margin:0;">
      由 Claude Sonnet + GitHub Actions 自动生成 &nbsp;·&nbsp; 数据来源：Google News RSS
    </p>
  </div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(full_html, "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(EMAIL_FROM, EMAIL_PASS)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

    print(f"✅ 邮件已发送至 {EMAIL_TO}，主题：{subject}")


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    now           = datetime.now(TZ_BEIJING)
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"📰 抓取 {yesterday_str} 的上海房产新闻...")
    items = fetch_yesterday_news()
    print(f"   共找到 {len(items)} 条")

    print("🤖 调用 Claude API 整理摘要...")
    html = summarize_with_claude(items, yesterday_str)

    print("📧 发送邮件...")
    send_email(html, yesterday_str, len(items))
    print("🎉 完成！")


if __name__ == "__main__":
    main()
