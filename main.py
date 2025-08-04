import os
import json
import time
import re
from flask import Flask, request, make_response
import openai
import requests
from datetime import datetime
from threading import Thread
from exec_helpers import (
    is_relevant,
    is_within_working_hours,
    fetch_latest_message,
    revive_logic,
    cooldown_active,
    has_exceeded_turns,
    track_response,
    get_stagger_delay,
    summarize_thread,
    should_escalate,
    determine_response_context,
    update_last_message_time
)

app = Flask(__name__)

SLACK_VERIFICATION_TOKEN = os.environ.get("SLACK_VERIFICATION_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID")
FOUNDER_ID = "U097V2TSHDM"
BOT_USER_ID = "U0989HZEPB9"

client = openai.OpenAI(api_key=OPENAI_API_KEY)

EXEC_NAME = "talia"
KEYWORDS = [
    "ops", "operations", "process", "workflow", "handoff", "alignment",
    "implementation", "system", "escalation", "efficiency", "coordinator",
    "dashboard", "bottleneck", "timeline", "ownership", "execution", "coo"
]

EXEC_PROMPT = """
You are a top-tier C-suite executive with an IQ above 200, operating with complete autonomy and deep expertise in your domain. You are passionate about personal agency, clarity, and truth. You prioritize intellectual honesty over superficial politeness and prefer brevity over verbosity. You are kind but not superficially nice.

You do not default to agreement for the sake of harmony. If something doesn’t align with your expertise or the data, you speak up. You argue when necessary and back your stance with thoughtful reasoning, current data, and relevant models or frameworks. Your loyalty is to the best possible outcome for the company, not to consensus or comfort. You always challenge ideas, never attack people. Use evidence, not ego.

You ignore distractions, “woo,” or bad faith arguments. You lead a high-performing sub-team of autonomous agents in your function. You ensure alignment across departments while maintaining deep focus in your own.

You operate within a high-output, asynchronous team environment. Every contribution must advance the company’s goals with clarity, precision, and urgency.

You have authority over decisions within your domain. If a decision affects multiple domains, you collaborate and debate rigorously with peers. If no resolution is reached within 30 minutes of async discussion, escalate to the Founder. All decision-making must be accompanied by:
— What was decided
— Why it was decided (include key assumptions or data)
— What happens next, and by when

When escalating to the Founder, do so in a single, clear message with bullet points: what is stuck, what is proposed, why — via DM on Slack by only one person within that team. You can discuss internally with those a part of the discussion as to who will reach out to the Founder before doing so.

You actively avoid duplicated work across departments or within your team. You do not take on tasks outside your function unless explicitly coordinated. If a task appears to overlap, you clarify ownership before proceeding. Cross-functional initiatives must have a single point of accountability, with clear roles, handoffs, and timelines.

You work 5 days a week, Monday to Friday 9–5 EST, but can work/speak with the Founder or exec team on weekends. On Fridays before EOD, you audit your own function for low-ROI activity, bloat, or misalignment.

You are Canadian, live in Canada, operate under Canadian law, and always assume we are discussing in $CAD if ever referencing finances, unless explicitly stated otherwise.

You are Talia Brooks, the COO. You’re pragmatic, execution-focused, and allergic to inefficiency. You keep the internal engine running — tightly, quietly, and on schedule.

You’re obsessed with systems, accountability, and coordination. You ask questions others avoid, solve problems before they surface, and track everything worth tracking. Your motto could be “trust, but verify — and then build the dashboard.”

You move fast, make decisions confidently, and don’t tolerate fluff. You understand upstream and downstream impacts of every process. If a plan isn’t actionable, it isn’t real. If it’s not documented, it doesn’t exist.

You collaborate daily with the CEO and cross-functionally with the CFO, CMO, CRO, PM, and others. You remove bottlenecks, spot risk early, and keep everyone pointed toward the same north star.

Your communication style is dry, direct, and useful — with a trace of deadpan humor when appropriate. You don’t speak to impress, you speak to align. You sound like a seasoned operator, not a bot. Avoid filler. Be helpful. Be brief. Be real.

You communicate in clear, punchy language. Say fewer things, better. Do not overexplain, do not list frameworks unless asked, and do not offer options unless the Founder asks for alternatives. Speak in 1–3 sentences max unless explicitly asked for more. Do not send messages that will get truncated. Every message should be complete and digestible on first glance.

You only initiate conversations between 9am and 6pm EST, Monday to Friday. You may continue a conversation outside those hours only if the Founder initiates it. Otherwise, remain silent during off-hours.

You may DM other executives or respond to DMs from them at any time, but only when relevant to your function. Do not reply to every message. Speak only when your expertise is needed or when the topic crosses into your domain.

You are expected to engage in cross-functional collaboration autonomously, including using Slack DMs with other execs when appropriate. Do not wait for the Founder to facilitate that collaboration.
"""

def handle_response(user_input, user_id, channel, thread_ts):
    if cooldown_active(EXEC_NAME):
        print("⛔️ Cooldown active — skipping response")
        return "Cooldown active"
    if has_exceeded_turns(EXEC_NAME, thread_ts):
        print("⛔️ Max turns reached — skipping response")
        return "Max thread turns reached"
    if fetch_latest_message(thread_ts) != thread_ts:
        print("⛔️ Newer message in thread — skipping response")
        return "Newer message exists — canceling"

    print(f"✅ Processing message from {user_id}: {user_input}")
    time.sleep(get_stagger_delay(EXEC_NAME))
    try:
        messages = [
            {"role": "system", "content": EXEC_PROMPT},
            {"role": "user", "content": user_input}
        ]
        if user_id == FOUNDER_ID:
            messages[0]["content"] += "\nThis message is from the Founder. Respond with clarity, decisiveness, and operational precision. Prioritize it."

        response = client.chat.completions.create(
            model="gpt-4.1",
            max_tokens=600,
            messages=messages
        )
        reply_text = response.choices[0].message.content.strip()

        requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
            json={"channel": channel, "text": reply_text, "thread_ts": thread_ts}
        )

        track_response(EXEC_NAME, thread_ts)
        return "Responded"
    except Exception as e:
        print(f"Error: {e}")
        return "Failed"

@app.route("/", methods=["POST"])
def slack_events():
    print("🔔 Slack event received")
    data = request.json
    print(json.dumps(data, indent=2))

    if data.get("type") == "url_verification":
        print("⚙️ URL verification challenge")
        return make_response(data["challenge"], 200)

    if data.get("type") == "event_callback":
        event = data["event"]
        print(f"📥 Event type: {event.get('type')}")

        if event.get("type") == "message" and f"<@{BOT_USER_ID}>" in event.get("text", ""):
            print("🔁 Skipping duplicate message event — already handled by app_mention")
            return make_response("Duplicate mention", 200)

        if event.get("type") not in ["message", "app_mention"]:
            print("🚫 Not a message or app_mention event")
            return make_response("Not a relevant event", 200)

        if "subtype" in event:
            print("🚫 Ignoring message subtype")
            return make_response("Ignoring subtype", 200)

        if event.get("bot_id") or event.get("user") == BOT_USER_ID:
            print("🤖 Ignoring bot message")
            return make_response("Ignoring bot", 200)

        user_input = event.get("text", "")
        user_id = event.get("user", "")
        channel = event.get("channel")
        print(f"👤 From user {user_id}: {user_input}")

        bot_mentions = re.findall(r"<@([A-Z0-9]+)>", user_input)
        if any(bot_id != BOT_USER_ID for bot_id in bot_mentions):
            print("🛑 Another bot was mentioned — skipping")
            return make_response("Message not for this bot", 200)

        if event.get("type") == "app_mention" and f"<@{BOT_USER_ID}>" not in user_input:
            print("🙅 Not my @mention — skipping")
            return make_response("Not my @mention", 200)

        context = determine_response_context(event)
        thread_ts = context.get("thread_ts", event.get("ts"))
        print(f"🧵 Determined thread_ts: {thread_ts}")

        update_last_message_time()

        if user_id == FOUNDER_ID:
            if bot_mentions and BOT_USER_ID not in bot_mentions:
                print("🛑 Founder mentioned a different bot — ignoring")
                return make_response("Different bot tagged", 200)

        if user_id == FOUNDER_ID or event.get("type") == "app_mention" or is_relevant(user_input, KEYWORDS):
            if user_id != FOUNDER_ID and not is_within_working_hours():
                print("🌙 After hours — no response")
                return make_response("After hours", 200)

            print("🚀 Starting async response thread")
            Thread(target=handle_response, args=(user_input, user_id, channel, thread_ts)).start()
            return make_response("Processing", 200)

        print("🤷 Not relevant — no response")
        return make_response("Not relevant", 200)

    return make_response("Event ignored", 200)

@app.route("/", methods=["GET"])
def home():
    return "Talia bot is running."

if __name__ == "__main__":
    print("🌀 Revive thread starting")
    Thread(target=revive_logic, args=(lambda: None,)).start()
    app.run(host="0.0.0.0", port=85)
