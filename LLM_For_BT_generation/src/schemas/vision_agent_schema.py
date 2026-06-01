from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum

class DefineBasicStates(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    FULL = "full"
    EMPTY = "empty"
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"

class SpatialZone(str, Enum):
    FOREGROUND = "foreground"    
    MIDGROUND  = "midground"     
    BACKGROUND = "background"    
    UNKNOWN    = "unknown"

class DetectedObject(BaseModel):
    name: str = Field(..., description="Object name, e.g. 'coffee machine', 'door', 'cup'")
    position: str = Field(..., description="Relative position, e.g. 'left side of room', 'on table'")
    #distance_meters: Optional[float] = Field(None, description="Estimated distance from robot in meters")
    zone: SpatialZone = SpatialZone.UNKNOWN 
    state: DefineBasicStates = Field(DefineBasicStates.UNKNOWN, description="Current observable state of the object")
    state_extra: Optional[str] = Field(None, description="Additional state details, e.g. 'crumpled', 'on desk', 'folded'")
    is_reachable: bool = Field(True, description="Whether the robot can navigate to this object")
    notes: Optional[str] = Field(None, description="Any relevant detail the VLM observed")
 
 
class DetectedPerson(BaseModel):
    position: str = Field(..., description="Where the person is in the scene")
    zone: SpatialZone = SpatialZone.UNKNOWN
    is_blocking_path: bool = Field(False, description="Whether the person is blocking the robot's path")
    activity: Optional[str] = Field(None, description="What the person appears to be doing")
 
 
class SceneDescription(BaseModel):

    raw_description: str = Field(..., description="Full natural language description from the VLM")
    
    objects: List[DetectedObject] = Field(
        default_factory=list,
        description="All objects detected in the scene"
    )
    persons: List[DetectedPerson] = Field(
        default_factory=list,
        description="People detected in the scene"
    )
    floor_clear: bool = Field(True, description="Whether the floor/path ahead is clear")
    lighting_condition: Optional[str] = Field(None, description="e.g. 'bright', 'dim', 'dark'")
    notes: Optional[str] = Field(None, description="Any additional observations from the VLM")

    def to_prompt_context(self) -> str:

        lines = []
        lines.append(f"**Description:** {self.raw_description}\n")
 
        if self.objects:
            zone_order = {"foreground": 0, "midground": 1, "background": 2, "unknown": 3}
            sorted_objects = sorted(self.objects, key=lambda o: zone_order[o.zone.value])
            lines.append("**Detected Objects:**")
            for obj in self.objects:
                reachable = "reachable" if obj.is_reachable else "NOT reachable"
                lines.append(
                                f"  - {obj.name}: {obj.position}, zone={obj.zone.value}, "
                                f"state={obj.state.value}, {reachable}"
                                + (f" — {obj.notes}" if obj.notes else "")
                            )
                
        if self.persons:
            lines.append("**People in scene:**")
            for p in self.persons:
                blocking = "BLOCKING PATH" if p.is_blocking_path else ""
                lines.append(
                    f"  - Person at {p.position}, zone={p.zone.value}{blocking}"
                    + (f", activity: {p.activity}" if p.activity else "")
                )

 
        lines.append(f"**Floor clear:** {'yes' if self.floor_clear else 'NO — obstacle detected'}")
        if self.lighting_condition:
            lines.append(f"**Lighting:** {self.lighting_condition}")
 
        return "\n".join(lines)

