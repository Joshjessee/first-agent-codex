from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape


@dataclass(frozen=True)
class DigestArticle:
    title: str
    source: str
    url: str
    summary: str
    why_it_matters: str


@dataclass(frozen=True)
class Digest:
    topic: str
    generated_at: datetime
    articles: list[DigestArticle]

    def subject(self, prefix: str) -> str:
        date_text = self.generated_at.strftime("%b %d, %Y")
        return f"{prefix}: {self.topic} - {date_text}"

    def to_text(self) -> str:
        lines = [
            f"Daily Research Digest: {self.topic}",
            f"Generated: {self.generated_at.strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        for index, article in enumerate(self.articles, start=1):
            lines.extend(
                [
                    f"{index}. {article.title}",
                    f"Source: {article.source}",
                    f"Link: {article.url}",
                    f"Summary: {article.summary}",
                    f"Why it matters: {article.why_it_matters}",
                    "",
                ]
            )

        return "\n".join(lines).strip() + "\n"

    def to_html(self) -> str:
        article_rows = "\n".join(
            f"""
            <tr>
              <td style="padding: 0 0 18px 0;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse: collapse; background: #ffffff; border: 1px solid #d9e2ec; border-radius: 8px;">
                  <tr>
                    <td style="padding: 20px 22px;">
                      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse: collapse;">
                        <tr>
                          <td style="width: 42px; vertical-align: top;">
                            <div style="width: 30px; height: 30px; line-height: 30px; border-radius: 50%; background: #0f766e; color: #ffffff; font-family: Arial, sans-serif; font-size: 14px; font-weight: 700; text-align: center;">{index}</div>
                          </td>
                          <td style="vertical-align: top;">
                            <div style="font-family: Arial, sans-serif; font-size: 12px; line-height: 18px; color: #64748b; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700;">{escape(article.source)}</div>
                            <h2 style="margin: 4px 0 10px 0; font-family: Arial, sans-serif; font-size: 21px; line-height: 28px; color: #102a43;">
                              <a href="{escape(article.url, quote=True)}" style="color: #102a43; text-decoration: none;">{escape(article.title)}</a>
                            </h2>
                            <p style="margin: 0 0 14px 0; font-family: Arial, sans-serif; font-size: 15px; line-height: 23px; color: #334e68;">{escape(article.summary)}</p>
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse: collapse; background: #f0fdfa; border-left: 4px solid #0f766e;">
                              <tr>
                                <td style="padding: 12px 14px;">
                                  <div style="font-family: Arial, sans-serif; font-size: 12px; line-height: 16px; color: #0f766e; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;">Why it matters</div>
                                  <div style="margin-top: 4px; font-family: Arial, sans-serif; font-size: 14px; line-height: 21px; color: #243b53;">{escape(article.why_it_matters)}</div>
                                </td>
                              </tr>
                            </table>
                            <div style="margin-top: 16px;">
                              <a href="{escape(article.url, quote=True)}" style="display: inline-block; background: #102a43; color: #ffffff; font-family: Arial, sans-serif; font-size: 14px; line-height: 18px; font-weight: 700; text-decoration: none; padding: 10px 14px; border-radius: 6px;">Read article</a>
                            </div>
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            """
            for index, article in enumerate(self.articles, start=1)
        )
        generated = self.generated_at.strftime("%A, %B %d, %Y at %I:%M %p")
        topic = escape(self.topic)
        return f"""
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Daily Research Digest: {topic}</title>
          </head>
          <body style="margin: 0; padding: 0; background: #eef2f6;">
            <div style="display: none; max-height: 0; overflow: hidden; opacity: 0;">
              Your top {len(self.articles)} articles for {topic}.
            </div>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse: collapse; background: #eef2f6;">
              <tr>
                <td align="center" style="padding: 28px 14px;">
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse: collapse; max-width: 720px;">
                    <tr>
                      <td style="padding: 26px 28px; background: #102a43; border-radius: 8px;">
                        <div style="font-family: Arial, sans-serif; font-size: 13px; line-height: 18px; color: #9fb3c8; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em;">Daily Research Digest</div>
                        <h1 style="margin: 8px 0 8px 0; font-family: Arial, sans-serif; font-size: 30px; line-height: 36px; color: #ffffff;">{topic}</h1>
                        <div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 21px; color: #bcccdc;">Generated {generated}</div>
                      </td>
                    </tr>
                    <tr>
                      <td style="padding: 20px 0 0 0;">
                        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse: collapse;">
                          {article_rows}
                        </table>
                      </td>
                    </tr>
                    <tr>
                      <td style="padding: 4px 2px 0 2px; font-family: Arial, sans-serif; font-size: 12px; line-height: 18px; color: #627d98;">
                        This digest was generated automatically from recent article candidates and ranked for relevance, timeliness, and significance.
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </body>
        </html>
        """
