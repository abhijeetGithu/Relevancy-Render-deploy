import openai
import os
import re
from typing import Optional

class GroundTruthAgent:
    """
    Agent for generating search queries from Title and Description using OpenAI GPT-4o-mini or Google Gemini API.
    """
    # List of keywords to consider for inclusion in queries
    # KEYWORDS = [
    #     "java", "rdbms", "sql", "mysql", "html", "ios", "android", "python", "reactjs", "angularjs",
    #     "selenium", "selenium-webdriver", "android-studio", "python-3.x", "openai-api", "angular",
    #     "flutter", "typescript", "javascript-objects", "prompt", "llm-sql-generation", "mcps", "mcpl",
    #     "user-agent", "google-chrome", "webdriver", "css", "elasticsearch", "google-analytics",
    #     "amazon-ecs", "aws-lambda", "aws-cloudformation", "aws-api-gateway", "aws-step-functions",
    #     "oracle-database", "stack", "fullcalendar", "mongodb", "postgresql", "django-postgresql",
    #     "amazon-redshift", "amazon-s3", "waf", "azure-devops", "azure"
    # ]
    
    def __init__(self, openai_api_key: Optional[str] = None, gemini_api_key: Optional[str] = None, 
                 model: str = "gpt-4o-mini", provider: str = "openai", custom_prompts: dict = None):
        """
        
        
        Args:
            openai_api_key: OpenAI API key (REQUIRED for OpenAI provider, no .env fallback)
            gemini_api_key: Google Gemini API key (REQUIRED for Gemini provider, no .env fallback)
            model: Model name to use (default: gpt-4o-mini for OpenAI, gemini-2.5-pro for Gemini)
            provider: LLM provider - 'openai' or 'gemini' (default: 'openai')
            custom_prompts: Dictionary of custom prompt templates (runtime only, optional)
        """
        self.provider = provider.lower()
        self.model = model
        self.custom_prompts = custom_prompts or {}  # Store custom prompts for runtime use
        
        if self.provider == "openai":
            # ONLY accept API key from parameter, NO environment variable fallback
            self.api_key = openai_api_key
            if not self.api_key:
                raise ValueError("OpenAI API key is required. Please provide it via the frontend UI.")
            self.client = openai.OpenAI(api_key=self.api_key)
            
        elif self.provider == "gemini":
            # ONLY accept API key from parameter, NO environment variable fallback
            self.api_key = gemini_api_key
            if not self.api_key:
                raise ValueError("Gemini API key is required. Please provide it via the frontend UI.")
            # Import google.genai (new SDK) only if using Gemini
            try:
                from google import genai
                self.client = genai.Client(api_key=self.api_key)
                # Default to gemini-2.5-pro if no model specified or if still using openai default
                if not model or model == "gpt-4o-mini":
                    self.model = "gemini-2.5-pro"
            except ImportError:
                raise ImportError("google-genai package not installed. Install with: pip install google-genai")
        else:
            raise ValueError(f"Unsupported provider: {provider}. Use 'openai' or 'gemini'")

    def _truncate_description(self, description: str, max_chars: int = 500) -> str:
        """
        Truncate description to fit within context limits for gpt-4o-mini.
        
        Args:
            description: Original description text . it just as bad it could be for your life and bread coudl also lead to many answers in himself to many answers 
            max_chars: Maximum characters to keep (default 1000 for gpt-4o-mini efficiency)
            
        Returns:
            Truncated description
        """
        if len(description) <= max_chars:
            return description
            
        # Truncate at word boundary
        truncated = description[:max_chars]
        last_space = truncated.rfind(' ')
        if last_space > max_chars * 0.8:  # Only truncate at word if we don't lose too much
            truncated = truncated[:last_space]
            
        return truncated + "..."

    def generate_search_query(self, title: str, description: str, difficulty: str = "medium", prompt_template: Optional[str] = None) -> str:
        """Generate a search query from title and description using OpenAI GPT-4o-mini.

        Args:
            title: The document title
            description: The document description
            difficulty: Prompt type - one of [
                "easy", "medium", "hard", "special_char", "keyword_based",
                "typo",               # introduce a realistic spelling mistake in one token
                "synonym_abbrev"      # include an abbreviation plus a synonym/related expansion
            ]
            prompt_template: Optional custom prompt template (overrides difficulty logic)

        Returns:
            Generated search query string
        """
        # Truncate description to manage context size for gpt-4o-mini
        truncated_description = self._truncate_description(description)
        
        if not prompt_template:
            # Check for custom prompts first (runtime override)
            normalized = (difficulty or "").strip().lower()
            if normalized in self.custom_prompts:
                # Use custom prompt and replace placeholders
                prompt_template = self.custom_prompts[normalized]
                prompt_template = prompt_template.replace('{title}', title).replace('{description}', truncated_description)
            else:
                # Use default prompts from code
                # Independent, self-contained prompt templates for each type
                if normalized == "special_char":
                    prompt_template = (
                    "You are a search relevance expert. Generate a query that includes special-character tokens "
                    " but only if they are explicitly present in the Title or Description.\n\n"
                    "Rules:\n"
                    "- First, scan the Title and Description for tokens containing special characters (e.g., CVE IDs, version numbers, error codes, standards like SOC2).\n"
                    "- If multiple special-character tokens exist, choose the one that appears most frequently or is most prominent in the text.\n"
                    "- Ensure the final query string contains the chosen special-character token explicitly.\n"
                    "- Always preserve the exact formatting of the token (e.g., 'cve-2025-0309', 'rbac v3', 'enable-tls13-early-data').\n"
                    "- Keep the query concise and natural, like what developers type when searching for CVEs, error codes, or version-specific issues.\n"
                    "- Do not output code snippets, configuration blocks, or long descriptive phrases.\n"
                    "- If no special-character tokens exist, generate a clean 4-5 word keyword query from the most relevant terms in the Title and Description, without introducing any artificial special characters.\n\n"
                    "- Include at least one word from the Title in the generated query\n"
                    f"Description: {truncated_description}\n\n"
                    "Return ONLY the query string."
                )
                elif normalized == "keyword_based":
                    prompt_template = (
                    "You are generating short keyword-based search queries from a given document title and description. Follow these rules carefully:\n"
                    "- The query must be very generic (not detailed or specific).\n"
                    "- Use 4-5 words only.\n"
                    "- You may combine a generic keyword with a relevant word from the document.\n"
                    "- The query should sometimes include extra noise by adding a generic but related word that beginners might search\n"
                    "- The query should roughly capture the intent of the document, but stay simple and keyword-oriented.\n\n"
                    "KEYWORD INTEGRATION STEP:\n"
                    f"- After generating the query, check if any of these keywords appear in the Title or Description\n"
                    "- If any keyword is found, add the most frequently occurring one to the beginning or end of your generated query.\n"
                    "- Only add the keyword if it actually appears in the Title and Description text.\n"
                    "- Example: If 'java' appears in the document and your query is 'string array', modify it to 'java string array' or 'string array java'.\n\n"
                    f"Title: {title}\n"
                    f"Description: {truncated_description}\n\n"
                    "Return ONLY the final query string with keyword integration applied."
                )
                elif normalized == "hard":
                    prompt_template = (
                    "You are a search relevance expert. Generate a HARD-level semantic search query that a user would type to find the given document.\n\n"
                    "HARD PROMPT RULES:\n"
                    "- Construct a query with 4-5 broad, common keywords.\n"
                    "- Focus only on the main technology, tool, or error category from the title or description.\n"
                    "- Do not include detailed jargon, version numbers, or specific methods.\n"
                    "- The query should feel like a beginner exploring or identifying the general problem for the first time.\n"
                    "- The query should somewhat match the document's overall intent and meaning (semantic relevance).\n"
                    "- Ensure the query reflects the document semantically, not just by copying exact words.\n\n"
                    f"Title: {title}\n"
                    f"Description: {truncated_description}\n\n"
                    "Return ONLY the query string."
                )
                elif normalized == "medium":
                    prompt_template = (
                    "You are a search relevance expert. Generate a MEDIUM-level search query that a user would type to find the given document.\n\n"
                    "MEDIUM PROMPT RULES:\n"
                    "- Construct a query with 4–5 keywords.\n"
                    "- Combine the general technology/tool with one specific feature, function, or common error mentioned in the title or description.\n"
                    "- Provide enough detail for troubleshooting or configuration, but keep it less specialized than an expert query.\n"
                    "- The query should feel like an intermediate developer searching for setup guidance or solving a moderately specific problem.\n\n"
                    f"Title: {title}\n"
                    f"Description: {truncated_description}\n\n"
                    "Return ONLY the final query string with keyword integration applied."
                )
                elif normalized == "typo":
                    prompt_template = (
                "You are a search relevance expert. Generate a SHORT search query relevant to the document.\n\n"
                "TYPO RULES:\n"
                "- Introduce realistic human spelling mistakes (common typos) in one or more non-acronym words.\n"
                "- Do NOT misspell acronyms or all-caps tokens (e.g., SQL, API, HTTP, AWS, REST).\n"
                "- Each typo must still leave the query understandable (e.g., 'pyhton', 'javsacript', 'mongdb').\n"
                "- You may introduce typos in multiple words if the query has more than two tokens.\n"
                "- All non-typo tokens must be correctly spelled.\n"
                "- Avoid adding extra punctuation or random characters.\n"
                "- If no suitable tokens to misspell, pick mid-length words (5–10 letters) from the Title/Description and swap two adjacent characters.\n\n"
                f"Title: {title}\n"
                f"Description: {truncated_description}\n\n"
                "Return ONLY the final query string (no explanations)."
                )
                elif normalized == "synonym_abbrev":
                    prompt_template = (
                "You are a search relevance expert. Generate ONE technical search query (3–5 tokens).\n\n"
                "RULES:\n"
                "1) The query MUST be generated using BOTH the Title and the Description. "
                "It is compulsory to consider the Description — do not generate the query from the Title alone.\n"
                "2) You MUST include both a synonym and an abbreviation in the generated query. "
                "The synonym and abbreviation may not always be explicitly present in the Title and Description, "
                "but they must be directly related to the Title and Description context. "
                "Actively look for words in the Title and Description and replace them with their related synonyms AND abbreviations. "
                "Do not hallucinate unrelated terms.\n"
                "You MUST use more than one synonym and more than one abbreviation in the generated query, and they must all be directly related to the Title and Description context.\n"
                "3) Do NOT add or invent unrelated words. No hallucination.\n"
                "4) No filler words (the, of, in, to, guide, tutorial, example).\n"
                "5) No numbers/versions unless they appear in Title/Description.\n"
                "6) Length: 3–5 tokens, concise and meaningful.\n\n"
                "REFERENCE EXAMPLES (for guidance only — do NOT copy them, they are only to illustrate the style of transformation):\n"
                "Original query: How to change filehandle with Python logging on the fly with different classes and imports\n"
                "Generated query: Switch fh in Py logging dynamically w/ multi cls & imports?\n\n"
                "Original query: Nested for loop that prints out the numbers backwards?\n"
                "Generated query: Inner loop to display nums rev order?\n\n"
                "Original query: Django: how to set log level to INFO or DEBUG\n"
                "Generated query: Django cfg log lvl INFO/DBG?\n\n"
                f"Title: {title}\n"
                f"Description: {truncated_description}\n\n"
                "IMPORTANT NOTE: Do NOT hallucinate or invent terms. "
                "These examples are provided only as references to show the expected style of abbreviation/synonym use. "
                "Your output must stay true to the meaning of BOTH the Title and the Description — "
                "using the Description is mandatory and not optional. "
                "It is compulsory that your query includes both a synonym and an abbreviation, "
                "derived only from the Title and Description context.\n\n"
                "Return ONLY the final query string."
                )




                else:  # easy
                    prompt_template = (
                    "You are a search relevance expert. Generate an EASY-level search query that a user would type to find the given document.\n\n"
                    "EASY PROMPT RULES:\n"
                    "- Construct a query with 6 or more keywords.\n"
                    "- Use the most specific technical terms from the title and description (frameworks, methods, error messages, configurations).\n"
                    "- Add 1–2 vague/noisy terms that show troubleshooting intent (e.g., not working, issue, problem, fix, why, how to).\n"
                    "- Do not stack vague terms (maximum 2).\n"
                    "- Do not invent unrelated technologies, product names, or random jargon not in the title/description.\n"
                    "- Keep vague terms natural to how developers search (avoid words like 'confusion' or 'confused').\n"
                    "- The final query should feel like a real expert developer's messy search: highly specific but with a small dose of vague words.\n\n"
                    f"Title: {title}\n"
                    f"Description: {truncated_description}\n\n"
                    "Return ONLY the final query string with keyword integration applied."
                )

        else:
            prompt_template = prompt_template.format(title=title, description=truncated_description)

        prompt = prompt_template
        
        try:
            normalized = (difficulty or "").strip().lower()
            
            if self.provider == "openai":
                # Generate content using OpenAI with reduced tokens for efficiency
                # Slightly lower temperature for "special_char" to preserve exact tokens
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=30,  # Reduced for shorter, focused queries
                    temperature=(0.35 if normalized == "synonym_abbrev" else (0.5 if normalized == "special_char" else 0.7)),
                    top_p=0.8,
                )
                
                # Extract and clean the query
                query = response.choices[0].message.content.strip()
                
            elif self.provider == "gemini":
                # Generate content using Google Gemini with new SDK
                from google.genai import types
                
                generation_config = types.GenerateContentConfig(
                    temperature=(0.35 if normalized == "synonym_abbrev" else (0.5 if normalized == "special_char" else 0.7)),
                    top_p=0.8,
                    max_output_tokens=30,
                )
                
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=generation_config
                )
                
                # Extract and clean the query from the new SDK response structure
                # The response contains candidates with parts
                if response and hasattr(response, 'candidates') and response.candidates:
                    candidate = response.candidates[0]
                    if hasattr(candidate, 'content') and candidate.content:
                        if hasattr(candidate.content, 'parts') and candidate.content.parts:
                            query = candidate.content.parts[0].text.strip()
                        else:
                            query = ""
                    else:
                        query = ""
                else:
                    query = ""
            
            # Remove any quotes, extra formatting, or prefixes
            query = query.replace('"', '').replace("'", "")
            if query.lower().startswith("search query:"):
                query = query[13:].strip()
            if query.lower().startswith("query:"):
                query = query[6:].strip()
                
            return query
            
        except Exception as e:
            print(f"Error generating search query with {self.provider.upper()}: {e}")
            return ""

    def generate_multiple_queries(self, title: str, description: str, count: int = 3, difficulties: list[str] = None) -> list[str]:
        """
        Generate multiple search queries for the same document with different difficulty levels.
        
        Args:
            title: The document title
            description: The document description 
            count: Number of queries to generate (default 3 for easy, medium, hard)
            difficulties: List of difficulty levels ["easy", "medium", "hard"]
            
        Returns:
            List of generated search queries
        """
        if difficulties is None:
            difficulties = ["easy", "medium", "hard"][:count]
        
        queries = []
        
        # Truncate description to manage context size
        truncated_description = self._truncate_description(description)
        
        prompt_template = """You are a search relevance expert. Generate {count} different search queries with varying difficulty levels for the given document.

DIFFICULTY LEVELS:
1. EASY: 2-3 broad keywords, general topic focus (e.g., "java error", "react component")
2. MEDIUM: 3-5 keywords, moderate specificity (e.g., "spring boot configuration", "react hooks")  
3. HARD: 4-6 specific technical keywords (e.g., "spring boot hibernate search partial matches")

REQUIREMENTS:
- Generate exactly {count} queries in order: {difficulty_order}
- Each query represents how users at different skill levels would search
- Use natural language that real users would type
- Keep queries concise (2-6 words typically)
- Focus on user search intent

Document Information:
Title: {title}
Description: {description}

Generate {count} queries (one per line, in order: {difficulty_order}):"""

        difficulty_order = " → ".join(difficulties)
        prompt = prompt_template.format(
            count=count, 
            title=title, 
            description=truncated_description,
            difficulty_order=difficulty_order
        )
        
        try:
            if self.provider == "openai":
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=100,  # Adjusted for multiple short queries
                    temperature=0.8,
                    top_p=0.9
                )
                
                # Parse multiple queries from response
                lines = response.choices[0].message.content.strip().split('\n')
                
            elif self.provider == "gemini":
                from google.genai import types
                
                generation_config = types.GenerateContentConfig(
                    temperature=0.8,
                    top_p=0.9,
                    max_output_tokens=100,
                )
                
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=generation_config
                )
                
                # Extract content from Gemini response
                content = ""
                if response and hasattr(response, 'candidates') and response.candidates:
                    candidate = response.candidates[0]
                    if hasattr(candidate, 'content') and candidate.content:
                        if hasattr(candidate.content, 'parts') and candidate.content.parts:
                            content = candidate.content.parts[0].text.strip()
                
                lines = content.split('\n') if content else []
            
            # Parse queries from lines (common for both providers)
            for line in lines:
                line = line.strip()
                # Clean up formatting (remove numbers, bullets, etc.)
                line = line.replace('"', '').replace("'", "")
                # Remove list markers
                import re
                line = re.sub(r'^\d+\.?\s*', '', line)  # Remove "1. " or "1 "
                line = re.sub(r'^[-*•]\s*', '', line)   # Remove "- " or "* "
                
                if line and len(line) > 2:  # Valid query
                    queries.append(line)
                    
            return queries[:count]  # Return only requested count
            
        except Exception as e:
            print(f"Error generating multiple search queries with {self.provider.upper()}: {e}")
            return []

    def judge_query_llm(self, title: str, description: str, query: str) -> tuple[int, str]:
        """
        Judge the relevance of a candidate query to a document using the LLM.

        Returns a tuple of (score: 1-5, explanation: str).
        """
        # Keep description concise for the judge as well
        truncated_description = self._truncate_description(description)

        judge_prompt = f"""
You are a search relevance judge.

Title: {title}
Description: {truncated_description}
Candidate Query: {query}

Please rate the relevance of the "Candidate Query" to the document on a scale from 1 to 5:
1 = Not relevant at all
5 = Highly relevant and well-aligned

Return only:
Score: <integer between 1 and 5>
Brief explanation (1 sentence):
"""
        try:
            if self.provider == "openai":
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": judge_prompt}],
                    max_tokens=60,
                    temperature=0.0,
                )
                content = response.choices[0].message.content.strip()
                
            elif self.provider == "gemini":
                from google.genai import types
                
                generation_config = types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=60,
                )
                
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=judge_prompt,
                    config=generation_config
                )
                
                # Extract content from Gemini response
                if response and hasattr(response, 'candidates') and response.candidates:
                    candidate = response.candidates[0]
                    if hasattr(candidate, 'content') and candidate.content:
                        if hasattr(candidate.content, 'parts') and candidate.content.parts:
                            content = candidate.content.parts[0].text.strip()
                        else:
                            content = ""
                    else:
                        content = ""
                else:
                    content = ""

            # Parse score 1-5
            score_match = re.search(r"Score:\s*([1-5])", content, flags=re.IGNORECASE)
            score = int(score_match.group(1)) if score_match else 0
            # Clamp just in case
            score = min(5, max(1, score)) if score else 0

            # Parse brief explanation
            expl_match = re.search(r"Brief\s*explanation\s*:?\s*(.*)", content, flags=re.IGNORECASE | re.DOTALL)
            if expl_match:
                explanation = expl_match.group(1).strip()
            else:
                # Fallback: take everything after the score line
                lines = [l.strip() for l in content.splitlines() if l.strip()]
                if lines:
                    # Remove the first line if it contains Score
                    if re.search(r"^Score\b", lines[0], flags=re.IGNORECASE):
                        explanation = " ".join(lines[1:]).strip()
                    else:
                        explanation = " ".join(lines).strip()
                else:
                    explanation = ""

            return score, explanation
        except Exception as e:
            return 0, f"Judge error: {e}"

    def generate_with_threshold(
        self,
        title: str,
        description: str,
        difficulty: str = "medium",
        threshold: int = 3,
        max_attempts: int = 3,
    ) -> dict:
        """
        Generate a query and have the LLM judge it. Regenerate up to max_attempts
        until the score >= threshold (default threshold=3). Returns a dict with
        query, score, explanation, attempts, met_threshold.
        """
        last_query: Optional[str] = None
        last_explanation: Optional[str] = None
        last_score: int = 0

        for attempt in range(1, max_attempts + 1):
            query = self.generate_search_query(title, description, difficulty)
            # If generation failed, still judge the empty string to proceed
            score, explanation = self.judge_query_llm(title, description, query)
            print(f"Attempt {attempt}: '{query}' | Score: {score} | Explanation: {explanation}")

            last_query, last_explanation, last_score = query, explanation, score

            if score >= threshold:
                return {
                    "query": query,
                    "score": score,
                    "explanation": explanation,
                    "attempts": attempt,
                    "met_threshold": True,
                }

        # If all attempts fail to meet the threshold
        return {
            "query": last_query,
            "score": last_score,
            "explanation": last_explanation,
            "attempts": max_attempts,
            "met_threshold": False,
        }
