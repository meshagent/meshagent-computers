# [Meshagent](https://www.meshagent.com)

## MeshAgent Computers

The ``meshagent.computers`` package defines abstractions for controlling browsers and operating systems and providing these abilities to agents. 

### ComputerAgent
The ComputerAgent in `meshagent-computers` extends the ``ChatBot`` with support for using browsers and computers. The computer agent will periodically send screenshots to participants on the thread using the MeshAgent messaging protocol, by sending a message of the type "computer_screen" and an attachment that contains a binary screenshot. 

```Python Python
from meshagent.api import RequiredToolkit
from meshagent.openai import OpenAIResponsesAdapter
from meshagent.computers import ComputerAgent, BrowserbaseBrowser, Operator
from meshagent.api.services import ServiceHost

service = ServiceHost()

@service.path("/computeragent")
class BrowserbaseAgent(ComputerAgent):
    def __init__(self):
        super().__init__(
            name="meshagent.browser",
            title="browser agent",
            description="a task runner that can use a browser",
            requires=[RequiredToolkit(name="ui", tools=[])],
            llm_adapter=OpenAIResponsesAdapter(
                model="computer-use-preview",
                response_options={"reasoning": {"generate_summary": "concise"}, "truncation": "auto"},
            ),
            labels=["tasks", "computers"],
            computer_cls=BrowserbaseBrowser,
            operator_cls=Operator
        )

asyncio.run(service.run())
```

---
### Learn more about MeshAgent on our website or check out the docs for additional examples!

**Website**: [www.meshagent.com](https://www.meshagent.com/)

**Documentation**: [docs.meshagent.com](https://docs.meshagent.com/)

---