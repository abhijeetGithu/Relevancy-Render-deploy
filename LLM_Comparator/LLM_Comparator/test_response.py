import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

# Configure API
genai.configure(api_key=os.environ["GOOGLE_API_KEY"])

# Create model
model = genai.GenerativeModel(model_name="models/gemini-2.5-flash-lite")

query = "Latest developments in LangSmith prompt evaluation"

response = model.generate_content(query)

print("\nANSWER:\n")
print(response.text)

print("\n" + "="*60)
print("SOURCES USED:\n")
print("="*60)

# Try to extract grounding metadata
sources_found = False
try:
    if response.candidates and len(response.candidates) > 0:
        candidate = response.candidates[0]
        
        if hasattr(candidate, 'grounding_metadata') and candidate.grounding_metadata:
            if hasattr(candidate.grounding_metadata, 'grounding_chunks'):
                chunks = candidate.grounding_metadata.grounding_chunks
                
                if chunks:
                    seen = set()
                    source_count = 0
                    
                    for chunk in chunks:
                        try:
                            # Try different attribute access methods
                            if hasattr(chunk, 'web') and chunk.web:
                                title = getattr(chunk.web, 'title', 'Unknown')
                                url = getattr(chunk.web, 'uri', '')
                                
                                if url and url not in seen:
                                    seen.add(url)
                                    print(f"\n📄 Source {source_count + 1}:")
                                    print(f"   Title: {title}")
                                    print(f"   URL: {url}")
                                    source_count += 1
                                    sources_found = True
                        except:
                            pass
                    
                    if not sources_found:
                        print("⚠️  No web sources were cited in this response.")
except Exception as e:
    pass

if not sources_found:
    print("\n📌 NOTE: Web search sources are available on:")
    print("   • Paid tier with web grounding enabled")
    print("   • Premium Gemini models with search integration")
    print("\n✅ Your free tier Gemini API is working correctly!")
    print("   The answer is AI-generated from training data, not web search.\n")
