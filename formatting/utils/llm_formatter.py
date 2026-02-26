"""
LLM integration for the template workflow only.

The previous approach (format_text_with_llm: segment raw text into blocks and inject_blocks)
has been removed. The current approach is:

- Build template from sample: utils.template_builder.sample_to_template
- Fill template with JSON: utils.template_filler.fill_template
- For LLM-assisted fill from case facts: use utils.template_filler.build_template_fill_prompt
  to build the prompt, call your LLM, then utils.template_filler.parse_llm_json_response
  to parse and validate the response before calling fill_template.
"""
