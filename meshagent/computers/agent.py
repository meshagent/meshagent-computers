from meshagent.openai import OpenAIResponsesAdapter
from meshagent.agents import LLMAdapter, AgentChatContext
from meshagent.tools import Tool, Toolkit, ToolContext
from meshagent.agents.prompt import PromptAgent
from meshagent.computers import Computer, Operator
from meshagent.agents.chat import ChatBot
from meshagent.api import RemoteParticipant, FileResponse
from meshagent.api.messaging import RawOutputs

from typing import Optional
import base64
import json

class ComputerAgent[ComputerType:Computer, OperatorType:Operator](ChatBot):
    def __init__(self, *, name, 
            title=None,
            description=None,
            requires=None,
            labels = None,
            computer_cls: ComputerType,
            operator_cls: OperatorType,
            rules: Optional[list[str]] = None,
            llm_adapter: Optional[LLMAdapter] = None,
            toolkits: list[Toolkit] = None
        ):

        if rules == None:
            rules=[
                "if asked to go to a URL, you MUST use the goto_url function to go to the url",
                "do not search for URLs, go to them",
                "first take action in the computer, before calling any functions"
            ]
        super().__init__(
            name=name,
            title=title,
            description=description,
            requires=requires,
            labels=labels,
            llm_adapter=llm_adapter,
            toolkits=toolkits,
            rules=rules
        )
        self.computer_cls = computer_cls
        self.operator_cls = operator_cls


    async def finalize_toolkits(self, *, toolkits: list[Toolkit], participant: RemoteParticipant, chat_context: AgentChatContext):
        

        operator : Operator = self.operator_cls()
        computer : Computer = self.computer_cls()
        started = False

        class ComputerTool(Tool):
            def __init__(self, *, operator: Operator, computer: Computer, title = "computer_call", description = "handle computer calls from computer use preview", rules = [], thumbnail_url = None, defs = None):
                super().__init__(
                    name="computer_call",
                    # TODO: give a correct schema
                    input_schema={
                        "additionalProperties" : False,
                        "type" : "object",
                        "required" : [],
                        "properties" : {}
                    },
                    title=title,
                    description=description,
                    rules=rules,
                    thumbnail_url=thumbnail_url,
                    defs=defs,
                   
                )
                self.computer = computer


            @property
            def options(self):
                return {
                    "type": "computer-preview",
                    "display_width": self.computer.dimensions[0],
                    "display_height": self.computer.dimensions[1],
                    "environment": self.computer.environment,
                }

            async def execute(self,  context: ToolContext, *, arguments):
                nonlocal started
                if started == False:
                    await self.computer.__aenter__()
                    started = True
            
                await context.room.agents.invoke_tool(toolkit="meshagent.ui", tool="show_toast", arguments={
                    "title" : "executing browser call",
                    "description" : json.dumps(arguments)
                }, participant_id=participant.id)

                outputs = await operator.play(computer=self.computer, item=arguments)
                for output in outputs:
                      if output["type"] == "computer_call_output":
                          if output["output"] != None:
                              if output["output"]["type"] == "input_image":
                                  
                                b64 : str = output["output"]["image_url"]
                                image_data_b64 = b64.split(",", 1)
                                
                                image_bytes = base64.b64decode(image_data_b64[1])

                           
                                await context.room.messaging.send_message(
                                    to=participant,
                                    type="computer_screen",
                                    message={
                                    },
                                    attachment=image_bytes
                                )

                return RawOutputs(outputs=outputs)
            
        class ScreenshotTool(Tool):
            def __init__(self, computer: Computer):
                self.computer = computer

                super().__init__(
                    name="screenshot",
                    # TODO: give a correct schema
                    input_schema={
                        "additionalProperties" : False,
                        "type" : "object",
                        "required" : ["full_page","save_path"],
                        "properties" : {
                            "full_page" : {
                                "type" : "boolean"
                            },
                            "save_path" : {
                                "type" : "string",
                                "description" : "a file path to save the screenshot to (should end with .png)"
                            }
                        }
                    },
                    description="take a screenshot of the current page",               
                )

            
            async def execute(self, context: ToolContext, save_path: str, full_page: bool):
                nonlocal started
                if started == False:
                    await self.computer.__aenter__()
                    started = True

                screenshot_bytes = await self.computer.screenshot_bytes(full_page=full_page)
                handle = await context.room.storage.open(path=save_path, overwrite=True)
                await context.room.storage.write(handle=handle, data=screenshot_bytes)
                await context.room.storage.close(handle=handle)

                return f"saved screenshot to {save_path}"
            
        class GotoURL(Tool):
            def __init__(self, computer: Computer):
                self.computer = computer

                super().__init__(
                    name="goto_url",
                    description="goes to a url in the browser",
                    # TODO: give a correct schema
                    input_schema={
                        "additionalProperties" : False,
                        "type" : "object",
                        "required" : ["url"],
                        "properties" : {
                            "url" : {
                                "type" : "string"
                            }
                        }
                    },
                )

            
            async def execute(self, context: ToolContext, url: str):
                nonlocal started
                if started == False:
                    await self.computer.__aenter__()
                    started = True

                if url.startswith("https://") == False:
                    url = "https://"+url

                await self.computer.goto(url)
        
        computer_tool = ComputerTool(computer=computer, operator=operator)
        
        computer_toolkit = Toolkit(name="meshagent.openai.computer", tools=[
            computer_tool,
            ScreenshotTool(computer=computer),
            GotoURL(computer=computer),
        ])

        outputs = await computer_tool.execute(context=ToolContext(
            room=self.room,
            caller=participant    
        ), arguments={
            "type" : "screenshot"
        })

        chat_context.messages.extend(outputs.outputs)

        return [
            computer_toolkit,
            *toolkits
        ]


