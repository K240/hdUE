#pragma once
#include "CoreMinimal.h"
#include "RHIResources.h"
class FRDGBuilder;
class FRDGTexture;
class FSceneView;
void HdUEDrawEnvironment(FRDGBuilder& GraphBuilder,const FSceneView& View,
    FRDGTexture* SceneColor,FRDGTexture* SceneDepth,FRHITexture* Environment,float Scale);
