#include "Modules/ModuleManager.h"
#include "ShaderCore.h"
#include "Misc/Paths.h"
class FHdueModule final:public FDefaultGameModuleImpl {
public:
    void StartupModule() override {
        AddShaderSourceDirectoryMapping(TEXT("/HdUE"),FPaths::Combine(FPaths::ProjectDir(),TEXT("Shaders")));
    }
};
IMPLEMENT_PRIMARY_GAME_MODULE(FHdueModule, HdUEBackend, "HdUEBackend");

