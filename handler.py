from modal import App, Image, Secret, Mount, web_endpoint

app = App()

handler_image = (
    Image.debian_slim()
    .pip_install("openai")
    .pip_install("supabase")
    .pip_install("playwright")
    .run_commands("playwright install && playwright install-deps")
)

@app.function(image=handler_image, secrets=[Secret.from_name("chatwithanywebsite-openai-key"), Secret.from_name("supabase_url"), Secret.from_name("supabase_key")])
@web_endpoint(method="POST")
def addwebsiteToKnowledge(req: dict):
    user_url: str = req["user_url"]
    print("Received request for: ", user_url)

    # Get PDF of website
    import asyncio
    from playwright.async_api import async_playwright
    from io import BytesIO
    async def auto_scroll(page):
        await page.evaluate('''
            async function() {
                await new Promise(resolve => {
                    let totalHeight = 0;
                    const distance = 100;
                    const timer = setInterval(() => {
                        const scrollHeight = document.body.scrollHeight;
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        if (totalHeight >= scrollHeight) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, 100);
                });
            }
        ''')
    
    pdf_file = None
    async def get_full_page_content(): 
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()

            await page.goto(user_url, wait_until='networkidle')
            await auto_scroll(page)

            pdf_stream = BytesIO()
            pdf_bytes = await page.pdf(format='A4', print_background=True)
            pdf_stream.write(pdf_bytes)

            # Ensure the buffer's position is at the start
            pdf_stream.seek(0)
            await browser.close()

            return pdf_stream
    
    try:
        pdf_file = asyncio.run(get_full_page_content())
        if not pdf_file:
            return "ERROR"
    except:
        return "ERROR"

    # Upload the user provided file to OpenAI
    from openai import OpenAI
    client = OpenAI()
    message_file = client.files.create(
        file=pdf_file, purpose="assistants"
    )
    print("Created file with ID: ", message_file.id)

    # Upload file ID to supabase
    import os
    from supabase import create_client
    supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    # Check if table already contains url
    supa.table("urlsToFiles").insert({"url": user_url, "fileID": message_file.id}).execute()
    response = supa.table("urlsToFiles").select("*").execute()
    print("Supa table: ", response)
    return message_file.id

@app.function(image=handler_image, secrets=[Secret.from_name("chatwithanywebsite-openai-key"), Secret.from_name("supabase_url"), Secret.from_name("supabase_key")])
@web_endpoint(method="POST")
def askWithKnowledge(req: dict):
    user_url: str = req["user_url"]
    user_query: str = req["user_query"]

    from openai import OpenAI
    client = OpenAI()

    assistant = client.beta.assistants.create(
        model="gpt-3.5-turbo",
        tools=[{"type": "file_search"}],
    )

    import os
    from supabase import create_client
    supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    response = supa.table("urlsToFiles").select("fileID").eq("url", user_url).execute()
    if len(response.data) == 0:
        return "Error: Could not find file"
    id = response.data[0]["fileID"]
    print("Found file ID: ", id)

    thread = client.beta.threads.create(
        messages=[
            {
                "role": "user",
                "content": user_query,
                # Attach the new file to the message.
                "attachments": [
                    { "file_id": id, "tools": [{"type": "file_search"}] }
                ],
            }
        ]
    )
    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread.id, assistant_id=assistant.id
    )

    messages = list(client.beta.threads.messages.list(thread_id=thread.id, run_id=run.id))

    message_content = messages[0].content[0].text
    annotations = message_content.annotations
    # citations = []
    for index, annotation in enumerate(annotations):
        message_content.value = message_content.value.replace(annotation.text, f"[{index}]")
        # if file_citation := getattr(annotation, "file_citation", None):
            # cited_file = client.files.retrieve(file_citation.file_id)
            # citations.append(f"[{index}] {cited_file.filename}")

    print(message_content.value)
    # print("\n".join(citations))
    return message_content.value
