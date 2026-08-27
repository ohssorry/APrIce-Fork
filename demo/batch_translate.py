 import anthropic

  client = anthropic.Anthropic()


  def batch_translate(documents):
      results = []
            for document in documents:
          results.append(
              client.messages.create(
                  model="claude-opus-5",
                  max_tokens=2048,
                  messages=[{"role": "user", "content": document}],
              )
          )

      return results
