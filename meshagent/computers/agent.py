from meshagent.openai import OpenAIResponsesAdapter
from meshagent.agents import LLMAdapter, AgentChatContext
from meshagent.tools import Tool, Toolkit, ToolContext
from meshagent.agents.prompt import PromptAgent
from meshagent.computers import Computer, Operator
from meshagent.agents.chat import ChatBot, ChatThreadContext
from meshagent.api import RemoteParticipant, FileResponse
from meshagent.api.messaging import RawOutputs

from typing import Optional
import base64
import json
import logging

logging.basicConfig()
logger = logging.getLogger("computer")
logger.setLevel(logging.INFO)

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
                "if asked to go to a URL, you MUST use the goto function to go to the url if it is available",
                "after going directly to a URL, the screen will change so you should take a look at it to know what to do next"
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


    async def init_thread_context(self, *, thread_context: ChatThreadContext):
        
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

                for participant in thread_context.participants:
                    await context.room.messaging.send_message(
                        to=participant,
                        type="computer_use",
                        message={
                            "arguments" : arguments
                        }
                    )

                outputs = await operator.play(computer=self.computer, item=arguments)
                for output in outputs:
                      if output["type"] == "computer_call_output":
                          if output["output"] != None:
                              if output["output"]["type"] == "input_image":
                                  
                                b64 : str = output["output"]["image_url"]
                                image_data_b64 = b64.split(",", 1)
                                
                                image_bytes = base64.b64decode(image_data_b64[1])

                                for participant in thread_context.participants:
                                    context.room.messaging.send_message_nowait(
                                        to=participant,
                                        type="computer_screen",
                                        message={
                                        },
                                        attachment=image_bytes
                                    )
                
                nonlocal computer_toolkit
                if len(computer_toolkit.tools) == 1:
                    # HACK: after looking at the page, add the other tools,
                    # if we add these first then the computer-use-preview mode fails if it calls them before using the computer
                    computer_toolkit.tools.extend([
                        ScreenshotTool(computer=computer),
                        GotoURL(computer=computer),
                    ])
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
                    name="goto",
                    description="goes to a specific URL. Make sure it starts with http:// or https://",
                    # TODO: give a correct schema
                    input_schema={
                        "additionalProperties" : False,
                        "type" : "object",
                        "required" : ["url"],
                        "properties" : {
                            "url" : {
                                "type" : "string",
                                "description": "Fully qualified URL to navigate to.",
                            }
                        }
                    },
                )

            
            async def execute(self, context: ToolContext, url: str):
                nonlocal started
                if started == False:
                    await self.computer.__aenter__()
                    started = True

                if url.startswith("https://") == False and url.startswith("http://") == False:
                    url = "https://"+url

                await self.computer.goto(url)

                # send an updated screen out
                for participant in thread_context.participants:
                    context.room.messaging.send_message_nowait(
                        to=participant,
                        type="computer_screen",
                        message={
                        },
                        attachment = await self.computer.screenshot_bytes(full_page=False)
                    )
        
        computer_tool = ComputerTool(computer=computer, operator=operator)
        
        computer_toolkit = Toolkit(name="meshagent.openai.computer", tools=[
            computer_tool
        ])

        thread_context.toolkits = [
            computer_toolkit,
            *thread_context.toolkits
        ]


