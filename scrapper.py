import requests

input_url = input("Enter the Reddit post URL (e.g., https://www.reddit.com/r/esGaming/comments/1asfw2u/con_un_presupuesto_de_unos_1000_qu%C3%A9_pc_me.json): ").strip()
url = input_url if input_url.endswith(".json") else input_url + ".json"

headers = {
    "User-Agent": "Mozilla/5.0 (Reddit scraper)"
}

response = requests.get(url, headers=headers)
data = response.json()

# =========================
# POST INFO
# =========================
post = data[0]["data"]["children"][0]["data"]

title = post["title"]
author = post["author"]
score = post["score"]
num_comments = post["num_comments"]

print("=" * 60)
print(f"📌 POST: {title}")
print(f"👤 {author} | ⬆ {score} | 💬 {num_comments} comments")
print("=" * 60)
print("\n💬 COMMENTS (ordenados por score de mayor a menor):\n")

# =========================
# COMMENTS
# =========================
comments = data[1]["data"]["children"]

def print_comment(comment, indent=0):
    if comment["kind"] != "t1":
        return

    c = comment["data"]
    author = c.get("author", "[deleted]")
    body = c.get("body", "")
    score = c.get("score", 0)
    replies = c.get("replies")

    prefix = "  " * indent

    print("────────────────────────────────────────")
    print(f"{prefix}👤 {author} | ⬆ {score}")
    print(f"{prefix}{body}")

    # replies (recursivo)
    if replies and isinstance(replies, dict):
        for r in replies["data"]["children"]:
            print_comment(r, indent + 1)


# ordenar por score
sorted_comments = sorted(
    [c for c in comments if c["kind"] == "t1"],
    key=lambda x: x["data"]["score"],
    reverse=True
)

for c in sorted_comments:
    print_comment(c)
    print()