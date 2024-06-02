from modal import App, Image, Secret, web_endpoint
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

app = App("chatwithanywebsite-handler")

auth_scheme = HTTPBearer()

handler_image = (
    Image.debian_slim()
    .pip_install("openai")
    .pip_install("supabase")
    .pip_install("playwright")
    .run_commands("playwright install && playwright install-deps")
)

@app.function(image=handler_image, secrets=[Secret.from_name("chatwithanywebsite"), Secret.from_name("chatwithanywebsite-openai-key"), Secret.from_name("supabase_url"), Secret.from_name("supabase_key")])
@web_endpoint(method="POST")
def addwebsiteToKnowledge(req: dict, token: HTTPAuthorizationCredentials = Depends(auth_scheme)):
    import os
    if token.credentials != os.environ["chatwithanywebsite"]:
        print("Received request with incorrect bearer token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_url: str = req["user_url"]
    print("Received request:")
    print(user_url)

    # Get PDF of website
    import asyncio
    from playwright.async_api import async_playwright
    
    pdf_file = None
    async def get_full_page_content(): 
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()

            await page.goto(user_url, wait_until='networkidle')
            await page.pdf(path="output.pdf", format='A4', outline=True, print_background=True)

            await browser.close()
            return True
        
    async def get_full_page_content_with_timeout():
        try:
            # Run the async function with a timeout of 25 seconds
            result = await asyncio.wait_for(get_full_page_content(), timeout=25.0)
            return result
        except asyncio.TimeoutError:
            print("Timed out")
            return False
    
    try:
        pdf = asyncio.run(get_full_page_content_with_timeout())
        if not pdf:
            return "Error connecting to website"
        pdf_file = open("output.pdf", "rb")
    except:
        return "Error connecting to website"

    # Upload the user provided file to OpenAI
    message_file = None
    try:
        from openai import OpenAI
        client = OpenAI()
        message_file = client.files.create(
            file=pdf_file, purpose="assistants"
        )
        print("Created file with ID: ", message_file.id)
    except:
        return "Error connecting to knowledge base"

    # Upload file ID to supabase
    try:
        import os
        from supabase import create_client
        supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
        # Check if table already contains url
        supa.table("urlsToFiles").insert({"url": user_url, "fileID": message_file.id}).execute()
        response = supa.table("urlsToFiles").select("*").execute()
        print("Supa table: ", response)
    except:
        return "Error connecting to database"

    return "Success"

@app.function(image=handler_image, secrets=[Secret.from_name("chatwithanywebsite"), Secret.from_name("chatwithanywebsite-openai-key"), Secret.from_name("supabase_url"), Secret.from_name("supabase_key")])
@web_endpoint(method="POST")
def askWithKnowledge(req: dict, token: HTTPAuthorizationCredentials = Depends(auth_scheme)):
    import os
    if token.credentials != os.environ["chatwithanywebsite"]:
        print("Received request with incorrect bearer token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_url: str = req["user_url"]
    user_query: str = req["user_query"]
    print("Received request:")
    print(user_url)
    print(user_query)

    from openai import OpenAI
    client = OpenAI()

    assistant = client.beta.assistants.create(
        model="gpt-4o",
        tools=[{"type": "file_search"}],
    )

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
                "role": "assistant",
                "content": """You are an agent designed to answer questions about websites.
                You will be given the website in a PDF file in your knowledge base
                and your job is to look at the website for relevant content that users ask about
                and respond with accurate information. Respond with detailed and lengthy information.
                Do not mention the website as a document, just answer directly as an agent for the website.
                Any user queries unrelated to the website should be rejected.
                """,
            },
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
    # annotations = message_content.annotations
    # citations = []
    # for index, annotation in enumerate(annotations):
    #     message_content.value = message_content.value.replace(annotation.text, f"[{index}]")
        # if file_citation := getattr(annotation, "file_citation", None):
            # cited_file = client.files.retrieve(file_citation.file_id)
            # citations.append(f"[{index}] {cited_file.filename}")

    print(message_content.value)
    # print("\n".join(citations))
    return message_content.value
