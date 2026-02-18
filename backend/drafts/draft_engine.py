from llm.client import LLMClient

llm = LLMClient()


class DraftEngine:

    def generate(self, prompts, case_data):
        result = {}
        for name, prompt in prompts.items():
            final_prompt = prompt + f"\n\nCase Data:\n{case_data}"
            result[name] = llm.generate(final_prompt)
        return result

    def generate_one_section(self, prompt: str, case_data: str) -> str:
        """Generate a single section. Used for streaming/progressive UI."""
        final_prompt = prompt + f"\n\nCase Data:\n{case_data}"
        return llm.generate(final_prompt)
