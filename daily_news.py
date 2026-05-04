#!/usr/bin/env python3
"""
上海房产日报 - 每日新闻抓取 + AI摘要 + 邮件推送
运行环境: GitHub Actions，每天北京时间 09:03 自动触发
新闻源: Bing News RSS（境外服务器抓中文新闻效果最佳）
"""

import os
import smtplib
import feedparser
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote
import anthropic

# ── 配置 ──────────────────────────────────────────────────────────────────────
SMTP_HOST       = "smtp.163.com"
SMTP_PORT       = 465
EMAIL_FROM      = "zikeliu27@163.com"
EMAIL_TO        = "zikeliu27@163.com"
EMAIL_PASS      = os.environ["EMAIL_PASS"]
CLAUDE_KEY      = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_BASE_URL = os.environ.get("CLAUDE_BASE_URL", "https://api.modelverse.cn")

TZ_BEIJING      = timezone(timedelta(hours=8))
TARGET_COUNT    = 20    # 目标文章数，不足时放宽到3天

# ── 新闻源：Bing News RSS（境外抓中文效果远好于 Google News）───────────────────
def _bing(q: str) -> str:
    return f"https://www.bing.com/news/search?q={quote(q)}&format=rss&mkt=zh-CN&setlang=zh-hans"

RSS_FEEDS = [
    _bing("上海房产政策限购"),
    _bing("上海楼市成交行情"),
    _bing("上海二手房网签挂牌"),
    _bing("上海新房开盘楼盘"),
    _bing("上海公积金贷款首付"),
    _bing("上海次新房限售解禁"),
    _bing("上海嘉定浦东宝山房价"),
    _bing("上海松江青浦闵行房价"),
    _bing("上海房价涨跌均价"),
    _bing("上海置换改善学区房"),
    _bing("上海五大新城规划"),
    _bing("上海购房刚需性价比"),
]

# ── 1. 抓取新闻（自动放宽日期直到凑够文章）──────────────────────────────────
def fetch_news() -> tuple[list[dict], str]:
    now = datetime.now(TZ_BEIJING)

    for days_back in (1, 2, 3):
        cutoff = (now - timedelta(days=days_back)).date()
        items, seen = [], set()

        for url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url, request_headers={
                    "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
                })
            except Exception as e:
                print(f"[WARN] 抓取失败: {url} → {e}")
                continue

            for entry in feed.entries:
                pub = entry.get("published_parsed")
                if pub:
                    pub_date = datetime(*pub[:6], tzinfo=timezone.utc) \
                               .astimezone(TZ_BEIJING).date()
                    if pub_date < cutoff:
                        continue

                title = entry.get("title", "").strip()
                if not title or title in seen:
                    continue
                seen.add(title)

                items.append({
                    "title":   title,
                    "link":    entry.get("link", ""),
                    "source":  entry.get("source", {}).get("title", "")
                               or entry.get("author", "未知来源"),
                    "summary": entry.get("summary", "")[:400],
                })

        print(f"   过去{days_back}天: {len(items)} 条")
        if len(items) >= TARGET_COUNT or days_back == 3:
            if days_back == 1:
                label = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                label = (f"{(now - timedelta(days=days_back)).strftime('%Y-%m-%d')}"
                         f" ~ {(now - timedelta(days=1)).strftime('%Y-%m-%d')}")
            return items, label

    return [], (now - timedelta(days=1)).strftime("%Y-%m-%d")


# ── 2. Claude API 整理摘要 ────────────────────────────────────────────────────
def summarize_with_claude(items: list[dict], date_str: str) -> str:
    client = anthropic.Anthropic(api_key=CLAUDE_KEY, base_url=CLAUDE_BASE_URL)

    if not items:
        return "<p style='color:#999;'>近期未检索到相关新闻，请检查网络或新闻源配置。</p>"

    news_text = "\n\n".join(
        f"【{i+1}】{item['title']}\n"
        f"来源：{item['source']}\n"
        f"链接：{item['link']}\n"
        f"摘要：{item['summary']}"
        for i, item in enumerate(items)
    )

    prompt = f"""以下是 {date_str} 关于上海房产的新闻（共{len(items)}条），请整理成结构化日报。

要求：
1. 按以下五类分组，无内容的分类直接省略：
   - 🏛 政策动向（限购、贷款、公积金、税费等官方政策）
   - 📊 市场行情（成交量、价格数据、涨跌趋势）
   - 🏗 楼盘动态（新盘开盘、在售项目、次新房解禁）
   - 🗺 区域聚焦（嘉定、浦东、宝山、松江、青浦等板块动态）
   - 💡 购房参考（置换、刚需、性价比、学区等实用信息）
2. 每条新闻一句话概括核心要点，末尾附链接：<a href="链接" style="color:#c0392b;">来源</a>
3. 重复或雷同的新闻合并成一条
4. 输出 HTML 格式（<h3> 分类标题，<ul><li> 列表条目）
5. 语言简洁，全程中文

新闻列表：
{news_text}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


# ── 3. 发送 HTML 邮件 ─────────────────────────────────────────────────────────
def send_email(html_body: str, date_str: str, news_count: int):
    subject = f"【上海房产日报】{date_str} · {news_count} 条资讯"

    full_html = f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"></head>
<body style="font-family:'PingFang SC',Arial,sans-serif;max-width:700px;
             margin:auto;padding:20px;color:#333;background:#f5f5f5;">
  <div style="background:#fff;border-radius:10px;padding:28px;
              box-shadow:0 2px 10px rgba(0,0,0,.08);">
    <h2 style="color:#c0392b;margin-top:0;padding-bottom:12px;
               border-bottom:2px solid #f0f0f0;">
      🏠 上海房产日报
      <span style="font-size:15px;color:#888;font-weight:normal;
                   margin-left:10px;">{date_str}</span>
    </h2>
    {html_body}
    <div style="margin-top:24px;padding-top:16px;border-top:1px solid #eee;
                color:#bbb;font-size:12px;">
      由 Claude Sonnet + GitHub Actions 自动生成 &nbsp;·&nbsp; 数据来源：Bing News
    </div>
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

    print(f"✅ 邮件已发送：{subject}")


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    print("📰 抓取上海房产新闻（Bing News RSS）...")
    items, date_str = fetch_news()
    print(f"   最终：{len(items)} 条，范围：{date_str}")

    print("🤖 调用 Claude API 整理摘要...")
    html = summarize_with_claude(items, date_str)

    print("📧 发送邮件...")
    send_email(html, date_str, len(items))
    print("🎉 完成！")


if __name__ == "__main__":
    main()
