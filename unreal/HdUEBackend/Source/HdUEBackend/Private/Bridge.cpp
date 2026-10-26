#include "CoreMinimal.h"
#if !WITH_EDITOR
#include "hdue/bridge.h"
#define UELIBRARY_DLL_EXPORT
#include "UELibraryAPI.h"
#undef UELIBRARY_DLL_EXPORT
#include "Scene.h"
#include "VirtualTextureResidency.h"
#include "NativePicking.h"
#include "hdue/material_api.h"
#include "hdue/depth_api.h"
#include "RenderUtils.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/World.h"
#include "CanvasTypes.h"
#include "ContentStreaming.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformTLS.h"
#include "LegacyScreenPercentageDriver.h"
#include "Math/PerspectiveMatrix.h"
#include "Math/Float16Color.h"
#include "Misc/Paths.h"
#include "Misc/ScopeLock.h"
#include "Modules/ModuleManager.h"
#include "RendererInterface.h"
#include "RenderingThread.h"
#include "RHIGPUReadback.h"
#include "RayTracingDefinitions.h"
#include "SceneManagement.h"
#include "SceneView.h"
#include "SceneViewExtension.h"
#include "SceneTexturesConfig.h"
#include "PostProcess/PostProcessInputs.h"
#include "RenderGraphBuilder.h"
#include "RenderGraphUtils.h"
#include "ScreenPass.h"
#include "TextureResource.h"
#include "UObject/Package.h"
#include "Runtime/Launch/Resources/Version.h"

static_assert(ENGINE_MAJOR_VERSION == HDUE_ENGINE_MAJOR &&
    ENGINE_MINOR_VERSION == HDUE_ENGINE_MINOR && ENGINE_PATCH_VERSION == HDUE_ENGINE_PATCH,
    "Use the pinned UE 5.8.4 source, not a different installed/source engine.");

namespace {
struct FReadbackJob {
    TAtomic<bool> Complete{false};
    TAtomic<bool> Failed{false};
    uint64 Generation = 0;
    uint32 Samples = 0;
    uint32 DrawIndex = 0;
    double Started = 0;
    HdueRenderRequest Request{};
    double RequestedAt = 0;
    bool Preparing = false;
    bool DepthEnabled = false;
    TAtomic<bool> DepthQueued{false};
};
struct FSession {
    TUniquePtr<FHdueScene> Scene;
    HdueSession Handle = 0;
    uint32 Width = 0, Height = 0;
    UTextureRenderTarget2D* Target = nullptr; // Rooted until DestroySession.
    FSceneViewStateInterface* ViewState = nullptr;
    HdueRenderRequest Request{};
    bool HasRequest = false;
    bool CameraCut = true;
    uint32 Draws = 0;
    double RequestedAt = 0;
    TSharedPtr<FReadbackJob, ESPMode::ThreadSafe> Pending;
    TUniquePtr<FRHIGPUTextureReadback> Readback; // Render Thread only; reused at this session's fixed size.
    TUniquePtr<FRHIGPUTextureReadback> DepthReadback; // Render Thread only.
    FCriticalSection FrameMutex;
    HdueFrameInfo Frame{};
    TArray<float> Pixels;
    TArray<float> DepthPixels;
    bool DepthEnabled = false;
    HdueResidencyOptions ResidencyOptions{sizeof(HdueResidencyOptions), 0, 120.0};
    TSharedPtr<FHdueVirtualTextureResidency, ESPMode::ThreadSafe> Residency;
    bool Preparing = false;
    bool ResidencyFailed = false;
};
// Attached only to hdUE's explicit view family. It receives the real scene
// depth after the Lumen or Path Tracer primary pass and before post processing.
class FHdueDepthCapture final : public FSceneViewExtensionBase {
    TSharedPtr<FSession,ESPMode::ThreadSafe> Session;
    TSharedPtr<FReadbackJob,ESPMode::ThreadSafe> Job;
public:
    FHdueDepthCapture(const FAutoRegister& AutoRegister,const TSharedPtr<FSession,ESPMode::ThreadSafe>& InSession,
        const TSharedPtr<FReadbackJob,ESPMode::ThreadSafe>& InJob) : FSceneViewExtensionBase(AutoRegister),Session(InSession),Job(InJob) {}
    void SetupViewFamily(FSceneViewFamily&) override {}
    void SetupView(FSceneViewFamily&,FSceneView&) override {}
    void BeginRenderViewFamily(FSceneViewFamily&) override {}
    bool IsActiveThisFrame_Internal(const FSceneViewExtensionContext&) const override { return false; }
    void PrePostProcessPass_RenderThread(FRDGBuilder& GraphBuilder,const FSceneView& View,const FPostProcessingInputs& Inputs) override {
        if(!Inputs.SceneTextures) return;
        FRDGTextureRef Source=Inputs.SceneTextures->GetParameters()->SceneDepthTexture;
        if(!Source) return;
        const FIntPoint Size(Session->Width,Session->Height);
        auto Desc=FRDGTextureDesc::Create2D(Size,PF_R32_FLOAT,FClearValueBinding::None,
            TexCreate_ShaderResource|TexCreate_RenderTargetable);
        FRDGTextureRef Target=GraphBuilder.CreateTexture(Desc,TEXT("Hdue.DeviceDepth"));
        AddDrawTexturePass(GraphBuilder,View,Source,Target,View.UnscaledViewRect.Min,FIntPoint::ZeroValue,Size);
        if(!Session->DepthReadback) Session->DepthReadback=MakeUnique<FRHIGPUTextureReadback>(TEXT("HdueDepth"));
        AddEnqueueCopyPass(GraphBuilder,Session->DepthReadback.Get(),Target);
        Job->DepthQueued.Store(true);
    }
};
struct FRuntime {
    uint32 Owner = 0;
    uint32 State = HDUE_STATE_COLD;
    bool EngineStarted = false;
    FString Error;
    UWorld* World = nullptr;
    uint64 NextSession = 1;
    uint64 NextFrame = 1;
    TSharedPtr<FSession, ESPMode::ThreadSafe> Session;
};
FRuntime Runtime;
IRendererModule& Renderer() { return FModuleManager::GetModuleChecked<IRendererModule>(TEXT("Renderer")); }

HdueResult Fail(HdueResult Code, const TCHAR* Message) {
    Runtime.Error = Message;
    return Code;
}
HdueResult CheckOwner() {
    return Runtime.Owner && Runtime.Owner != FPlatformTLS::GetCurrentThreadId()
        ? HDUE_WRONG_THREAD : HDUE_OK;
}
HdueResult CheckReady() {
    if (CheckOwner() != HDUE_OK) return HDUE_WRONG_THREAD;
    return Runtime.State == HDUE_STATE_READY ? HDUE_OK : HDUE_INVALID_STATE;
}
bool ValidSession(HdueSession Handle) {
    return Runtime.Session.IsValid() && Runtime.Session->Handle == Handle;
}
bool HasRayTracing() {
#if RHI_RAYTRACING
    return IsRayTracingEnabled() && GRHISupportsRayTracing && GRHISupportsRayTracingShaders;
#else
    return false;
#endif
}
void ReleaseResidency(const TSharedPtr<FSession, ESPMode::ThreadSafe>& Session) {
    if (auto Residency = Session->Residency) {
        ENQUEUE_RENDER_COMMAND(HdueReleaseResidency)([Residency](FRHICommandListImmediate&) { Residency->Release(); });
        Session->Residency.Reset();
    }
    Session->Preparing = false;
    Session->ResidencyFailed = false;
}

HdueResult HDUE_CALL GetStatus(HdueStatus* Status) {
    if (!Status || Status->struct_size != sizeof(*Status)) return HDUE_INVALID_ARGUMENT;
    if (CheckOwner() != HDUE_OK) return HDUE_WRONG_THREAD;
    HdueStatus Result{};
    Result.struct_size = sizeof(Result);
    Result.state = Runtime.State;
    Result.world_ready = Runtime.World && Runtime.World->Scene;
    Result.rhi_ready = GDynamicRHI && !GUsingNullRHI;
    Result.engine_major = ENGINE_MAJOR_VERSION;
    Result.engine_minor = ENGINE_MINOR_VERSION;
    Result.engine_patch = ENGINE_PATCH_VERSION;
    if (Result.rhi_ready) Result.dedicated_video_memory_bytes = GRHIGlobals.GpuInfo.DedicatedVideoMemory;
    Result.render_mode_mask = 1u << HDUE_REALTIME;
    if (Result.rhi_ready && HasRayTracing()) Result.render_mode_mask |= 1u << HDUE_PATH_TRACED;
    FTCHARToUTF8 Message(*Runtime.Error);
    const int32 Count = FMath::Min(Message.Length(), int32(sizeof(Result.message) - 1));
    FMemory::Memcpy(Result.message, Message.Get(), Count);
    *Status = Result;
    return HDUE_OK;
}

HdueResult HDUE_CALL Initialize(const HdueInitDesc* Desc) {
    if (!Desc || Desc->struct_size != sizeof(*Desc) || !Desc->instance_handle || !Desc->window_handle ||
        !Desc->project_path || !Desc->map_path) return HDUE_INVALID_ARGUMENT;
    if (Runtime.State != HDUE_STATE_COLD) return HDUE_INVALID_STATE;
    Runtime.Owner = FPlatformTLS::GetCurrentThreadId();
    Runtime.State = HDUE_STATE_STARTING;
    const FString Project = UTF8_TO_TCHAR(Desc->project_path);
    const FString Map = UTF8_TO_TCHAR(Desc->map_path);
    if (!FPaths::FileExists(Project) || Project.Contains(TEXT("\"")) || Map.Contains(TEXT("\""))) {
        Runtime.State = HDUE_STATE_FAILED;
        return Fail(HDUE_INVALID_ARGUMENT, TEXT("Project not found, or invalid project/map quoting."));
    }
    FString Command = FString::Printf(TEXT("\"%s\" %s -game -windowed -d3d12 -unattended -nosplash -nosound -NoLoadingScreen -NoSplash -ResX=640 -ResY=360"), *Project, *Map);
    if (Desc->extra_arguments) Command += FString(TEXT(" ")) + UTF8_TO_TCHAR(Desc->extra_arguments);
    const int Code = UELibrary_InitW(reinterpret_cast<HINSTANCE>(Desc->instance_handle),
        reinterpret_cast<HWND>(Desc->window_handle), *Command);
    Runtime.EngineStarted = Code == 0;
    // UELibrary's return value alone does not validate LaunchWindowsStartup.
    if (Code != 0 || !GEngine || !GDynamicRHI || GUsingNullRHI) {
        Runtime.State = HDUE_STATE_FAILED;
        return Fail(HDUE_ENGINE_ERROR, TEXT("UELibrary did not initialize an Engine and a rendering RHI."));
    }
    for (const FWorldContext& Context : GEngine->GetWorldContexts()) {
        if (Context.WorldType == EWorldType::Game && Context.World()) {
            Runtime.World = Context.World();
            break;
        }
    }
    if (!Runtime.World || !Runtime.World->Scene) {
        Runtime.State = HDUE_STATE_FAILED;
        return Fail(HDUE_ENGINE_ERROR, TEXT("No game World/Scene after UELibrary initialization. Cook and stage the test map first."));
    }
    // Only the explicit offscreen view family should render our world.
    if (GEngine->GameViewport) GEngine->GameViewport->bDisableWorldRendering = true;
    if (IConsoleVariable* CVar = IConsoleManager::Get().FindConsoleVariable(TEXT("t.IdleWhenNotForeground"))) CVar->Set(0, ECVF_SetByCode);
    Runtime.State = HDUE_STATE_READY;
    Runtime.Error.Empty();
    return HDUE_OK;
}

HdueResult HDUE_CALL DestroySession(HdueSession Handle) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle)) return HDUE_INVALID_ARGUMENT;
    const auto Session = Runtime.Session;
    ReleaseResidency(Session);
    Session->Scene.Reset();
    // Drain callbacks before releasing rooted UObjects or the persistent view.
    FlushRenderingCommands();
    ENQUEUE_RENDER_COMMAND(HdueReleaseReadback)([Session](FRHICommandListImmediate&) { Session->Readback.Reset(); Session->DepthReadback.Reset(); });
    FlushRenderingCommands();
    Session->Pending.Reset();
    Session->ViewState->Destroy();
    Session->ViewState = nullptr;
    Session->Target->RemoveFromRoot();
    Session->Target = nullptr;
    Runtime.Session.Reset();
    return HDUE_OK;
}

HdueResult HDUE_CALL CreateSession(const HdueSessionDesc* Desc, HdueSession* Handle) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!Desc || Desc->struct_size != sizeof(*Desc) || !Handle || !Desc->width || !Desc->height ||
        Desc->width > 8192 || Desc->height > 8192) return HDUE_INVALID_ARGUMENT;
    if (Runtime.Session) return Fail(HDUE_INVALID_STATE, TEXT("The initial backend supports one active session."));
    auto Session = MakeShared<FSession, ESPMode::ThreadSafe>();
    Session->Handle = Runtime.NextSession++;
    Session->Width = Desc->width;
    Session->Height = Desc->height;
    Session->Scene = MakeUnique<FHdueScene>(Runtime.World);
    Session->Target = NewObject<UTextureRenderTarget2D>(GetTransientPackage());
    Session->Target->AddToRoot();
    Session->Target->ClearColor = FLinearColor::Black;
    Session->Target->bAutoGenerateMips = false;
    Session->Target->InitCustomFormat(Desc->width, Desc->height, PF_FloatRGBA, true);
    Session->Target->UpdateResourceImmediate(true);
    Session->ViewState = Renderer().AllocateViewState(Runtime.World->GetFeatureLevel());
    *Handle = Session->Handle;
    Runtime.Session = Session;
    return HDUE_OK;
}

HdueResult HDUE_CALL RequestRender(HdueSession Handle, const HdueRenderRequest* Request) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Request || Request->struct_size != sizeof(*Request) || !Request->generation ||
        Request->mode > HDUE_PATH_TRACED || Request->samples_per_pixel == 0 || Request->samples_per_pixel > 65536 ||
        !FMath::IsFinite(Request->horizontal_fov_degrees) || Request->horizontal_fov_degrees < 1 || Request->horizontal_fov_degrees > 170 ||
        !FMath::IsFinite(Request->near_clip_cm) || Request->near_clip_cm <= 0 || !FMath::IsFinite(Request->time_seconds)) return HDUE_INVALID_ARGUMENT;
    for (int32 Index = 0; Index < 3; ++Index) {
        if (!FMath::IsFinite(Request->eye[Index]) || !FMath::IsFinite(Request->target[Index]) || !FMath::IsFinite(Request->up[Index])) return HDUE_INVALID_ARGUMENT;
    }
    const FVector Eye(Request->eye[0], Request->eye[1], Request->eye[2]);
    const FVector Target(Request->target[0], Request->target[1], Request->target[2]);
    const FVector Up(Request->up[0], Request->up[1], Request->up[2]);
    if (Eye.Equals(Target) || FVector::CrossProduct(Target - Eye, Up).IsNearlyZero()) return HDUE_INVALID_ARGUMENT;
    if (Request->mode == HDUE_PATH_TRACED && !HasRayTracing()) return HDUE_NOT_SUPPORTED;
    const auto Session = Runtime.Session;
    if (Request->generation <= Session->Request.generation) return HDUE_STALE_FRAME;
    HdueAssetSceneStatus Assets{}; Session->Scene->Assets.GetSceneStatus(Assets);
    if (Assets.loading) return HDUE_PENDING;
    if (Assets.failed) return Fail(HDUE_ENGINE_ERROR, UTF8_TO_TCHAR(Assets.message));
    ReleaseResidency(Session);
    // A moving realtime camera needs Lumen's persistent temporal history. Scene
    // edits cancel HasRequest in ApplyScene; mode/time/settings changes still
    // reset, and every Path Tracer request starts a fresh accumulation.
    const auto& Previous = Session->Request;
    const bool KeepHistory = Session->HasRequest && Previous.mode == HDUE_REALTIME && Request->mode == HDUE_REALTIME &&
        Previous.time_seconds == Request->time_seconds && Previous.lumen_enabled == Request->lumen_enabled &&
        Previous.samples_per_pixel == Request->samples_per_pixel && Previous.warmup_frames == Request->warmup_frames;
    if (!KeepHistory) {
        FlushRenderingCommands();
        Session->ViewState->Destroy();
        Session->ViewState = Renderer().AllocateViewState(Runtime.World->GetFeatureLevel());
    }
    Session->CameraCut = !KeepHistory;
    Session->Request = *Request;
    Session->Scene->UpdateCameraLights(Eye, Target, Up);
    Session->Draws = 0;
    Session->HasRequest = true;
    Session->RequestedAt = FPlatformTime::Seconds();
    Runtime.Error.Empty();
    if (Request->mode == HDUE_PATH_TRACED) {
        Session->Residency = MakeShared<FHdueVirtualTextureResidency, ESPMode::ThreadSafe>(
            Session->Scene->GatherVirtualTextures(), Request->generation, Session->ResidencyOptions);
        Session->Preparing = Session->Residency->Status().phase != HDUE_RESIDENCY_READY;
    }
    Runtime.World->SetShouldTick(Request->mode == HDUE_REALTIME);
    if (IConsoleVariable* CVar = IConsoleManager::Get().FindConsoleVariable(TEXT("r.PathTracing.SamplesPerPixel"))) CVar->Set(int32(Request->samples_per_pixel), ECVF_SetByCode);
    if (IConsoleVariable* CVar = IConsoleManager::Get().FindConsoleVariable(TEXT("r.PathTracing.Denoiser"))) CVar->Set(0, ECVF_SetByCode);
    return HDUE_OK;
}

void Draw(const TSharedPtr<FSession, ESPMode::ThreadSafe>& Session) {
    const auto& Request = Session->Request;
    const bool PathTracing = Request.mode == HDUE_PATH_TRACED && !Session->Preparing;
    if (Session->Preparing) {
        const auto Residency = Session->Residency;
        ENQUEUE_RENDER_COMMAND(HduePrepareResidency)([Residency](FRHICommandListImmediate&) { Residency->Update(); });
    }
    FEngineShowFlags Flags(ESFIM_Game);
    Flags.SetPathTracing(PathTracing);
    Flags.SetScreenPercentage(false);
    Flags.SetTonemapper(false);
    Flags.SetEyeAdaptation(false);
    Flags.SetRefraction(true);
    auto* Resource = Session->Target->GameThread_GetRenderTargetResource();
    FSceneViewFamilyContext Family(FSceneViewFamily::ConstructionValues(Resource, Runtime.World->Scene, Flags)
        .SetRealtimeUpdate(true).SetResolveScene(true)
        .SetTime(FGameTime::CreateDilated(FPlatformTime::Seconds() - GStartTime, 1.0f / 60,
            Request.time_seconds, Request.mode == HDUE_PATH_TRACED ? 0.0f : 1.0f / 60)));
    FSceneViewInitOptions Options;
    Options.ViewFamily = &Family;
    Options.SceneViewStateInterface = Session->ViewState;
    Options.SetViewRectangle(FIntRect(0, 0, Session->Width, Session->Height));
    Options.ViewOrigin = FVector(Request.eye[0], Request.eye[1], Request.eye[2]);
    IStreamingManager::Get().AddViewInformation(Options.ViewOrigin, float(Session->Width),
        float(Session->Width) / FMath::Tan(FMath::DegreesToRadians(Request.horizontal_fov_degrees * .5f)),
        1.f, false, 1.f, nullptr, Runtime.World);
    const FVector Target(Request.target[0], Request.target[1], Request.target[2]);
    const FVector Up(Request.up[0], Request.up[1], Request.up[2]);
    Options.ViewRotationMatrix = FRotationMatrix::MakeFromXZ(Target - Options.ViewOrigin, Up).GetTransposed() * FMatrix(
        FPlane(0, 0, 1, 0), FPlane(1, 0, 0, 0), FPlane(0, 1, 0, 0), FPlane(0, 0, 0, 1));
    Options.ProjectionMatrix = FReversedZPerspectiveMatrix(double(FMath::DegreesToRadians(Request.horizontal_fov_degrees) * 0.5),
        double(Session->Width), double(Session->Height), double(Request.near_clip_cm));
    Options.BackgroundColor = FLinearColor::Black;
    FSceneView* View = new FSceneView(Options);
    View->bIsGameView = true;
    View->bIsOfflineRender = PathTracing;
    View->bCameraCut = Session->CameraCut && Session->Draws == 0;
    View->StartFinalPostprocessSettings(Options.ViewOrigin);
    View->FinalPostProcessSettings.AutoExposureMethod = AEM_Manual;
    View->FinalPostProcessSettings.AutoExposureBias = 0;
    View->FinalPostProcessSettings.DynamicGlobalIlluminationMethod = Request.lumen_enabled ? EDynamicGlobalIlluminationMethod::Lumen : EDynamicGlobalIlluminationMethod::None;
    View->FinalPostProcessSettings.ReflectionMethod = Request.lumen_enabled ? EReflectionMethod::Lumen : EReflectionMethod::None;
    View->FinalPostProcessSettings.PathTracingSamplesPerPixel = Request.samples_per_pixel;
    View->FinalPostProcessSettings.PathTracingMaxBounces = Session->Scene->HasRefractiveMaterials() ? 16 : 8;
    View->FinalPostProcessSettings.LumenFrontLayerTranslucencyReflections = true;
    View->EndFinalPostprocessSettings(Options);
    Family.Views.Add(View);
    Family.SetScreenPercentageInterface(new FLegacyScreenPercentageDriver(Family, 1.0f));
    FCanvas Canvas(Resource, nullptr, Runtime.World, Runtime.World->GetFeatureLevel());
    const auto Job = MakeShared<FReadbackJob, ESPMode::ThreadSafe>();
    Job->Generation = Request.generation;
    Job->DrawIndex = ++Session->Draws;
    Job->Started = FPlatformTime::Seconds();
    Job->Request = Request;
    Job->RequestedAt = Session->RequestedAt;
    Job->Preparing = Session->Preparing;
    Job->DepthEnabled = Session->DepthEnabled;
    Session->Pending = Job;
    if(Job->DepthEnabled) Family.ViewExtensions.Add(FSceneViewExtensions::NewExtension<FHdueDepthCapture>(Session,Job));
    Renderer().BeginRenderingViewFamily(&Canvas, &Family);
    Canvas.Flush_GameThread();
    const auto State = Session->ViewState;
    ENQUEUE_RENDER_COMMAND(HdueCopyColor)([Session, Job, Resource, State, PathTracing](FRHICommandListImmediate& Commands) {
        if (!Session->Readback) Session->Readback = MakeUnique<FRHIGPUTextureReadback>(TEXT("HdueColor"));
        Session->Readback->EnqueueCopy(Commands, Resource->GetRenderTargetTexture());
        if(Job->DepthEnabled && !Job->DepthQueued.Load()) { Job->Failed.Store(true); Job->Complete.Store(true); }
#if RHI_RAYTRACING
        Job->Samples = PathTracing ? State->GetPathTracingSampleIndex() : 0;
#endif
    });
}

void PollReadback(const TSharedPtr<FSession, ESPMode::ThreadSafe>& Session) {
    const auto Job = Session->Pending;
    const uint64 FrameId = Runtime.NextFrame++;
    const uint32 Width = Session->Width, Height = Session->Height;
    const auto Request = Job->Request;
    const double RequestedAt = Job->RequestedAt;
    ENQUEUE_RENDER_COMMAND(HduePollReadback)([Session, Job, FrameId, Width, Height, Request, RequestedAt](FRHICommandListImmediate&) {
        if (Job->Complete.Load() || !Session->Readback || !Session->Readback->IsReady()) return;
        if(Job->DepthEnabled && (!Session->DepthReadback || !Session->DepthReadback->IsReady())) return;
        int32 Pitch = 0, BufferHeight = 0;
        const auto* Data = static_cast<const FFloat16Color*>(Session->Readback->Lock(Pitch, &BufferHeight));
        int32 DepthPitch=0,DepthHeight=0;
        const auto* DepthData=Job->DepthEnabled ? static_cast<const float*>(Session->DepthReadback->Lock(DepthPitch,&DepthHeight)) : nullptr;
        const bool DepthValid=!Job->DepthEnabled || (DepthData && DepthPitch>=int32(Width) && DepthHeight>=int32(Height));
        if (Data && Pitch >= int32(Width) && BufferHeight >= int32(Height) && DepthValid) {
            TArray<float> Pixels;
            TArray<float> DepthPixels;
            if(Job->DepthEnabled) DepthPixels.SetNumUninitialized(int64(Width)*Height);
            Pixels.SetNumUninitialized(int64(Width) * Height * 4);
            for (uint32 Y = 0; Y < Height; ++Y) {
                for (uint32 X = 0; X < Width; ++X) {
                    const auto& P = Data[uint64(Y) * Pitch + X];
                    const uint64 Index = (uint64(Y) * Width + X) * 4;
                    Pixels[Index] = P.R.GetFloat(); Pixels[Index + 1] = P.G.GetFloat();
                    Pixels[Index + 2] = P.B.GetFloat(); Pixels[Index + 3] = P.A.GetFloat();
                    if(DepthData) {
                        const float Z=DepthData[uint64(Y)*DepthPitch+X];
                        DepthPixels[uint64(Y)*Width+X]=Z>0.f && FMath::IsFinite(Z) ? Request.near_clip_cm/Z : 0.f;
                    }
                }
            }
            const double Now = FPlatformTime::Seconds();
            HdueFrameInfo Info{};
            Info.struct_size = sizeof(Info); Info.width = Width; Info.height = Height;
            Info.pixel_format = HDUE_RGBA32_FLOAT; Info.color_space = HDUE_SCENE_LINEAR_SRGB;
            Info.row_stride_bytes = Width * 4 * sizeof(float);
            Info.generation = Job->Generation; Info.frame_id = FrameId;
            Info.required_bytes = uint64(Width) * Height * 4 * sizeof(float);
            Info.completed_samples = Job->Samples;
            Info.converged = !Job->Preparing && (Request.mode == HDUE_PATH_TRACED
                ? Job->Samples >= Request.samples_per_pixel : Job->DrawIndex >= FMath::Max(1u, Request.warmup_frames));
            Info.request_to_frame_ms = (Now - RequestedAt) * 1000;
            Info.readback_ms = (Now - Job->Started) * 1000;
            FScopeLock Lock(&Session->FrameMutex);
            Session->Pixels = MoveTemp(Pixels);
            Session->DepthPixels = MoveTemp(DepthPixels);
            Session->Frame = Info;
        } else {
            Job->Failed.Store(true);
        }
        if (Data) Session->Readback->Unlock();
        if(DepthData) Session->DepthReadback->Unlock();
        Job->Complete.Store(true);
    });
}

HdueResult HDUE_CALL Tick() {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (UELibrary_Tick() != 0) return Fail(HDUE_ENGINE_ERROR, TEXT("UELibrary_Tick failed."));
    const auto Session = Runtime.Session;
    if (Session) Session->Scene->Assets.Tick();
    if (!Session || !Session->HasRequest) return HDUE_OK;
    if (Session->Pending) {
        if (!Session->Pending->Complete.Load()) { PollReadback(Session); return HDUE_OK; }
        if (Session->Pending->Failed.Load()) return Fail(HDUE_ENGINE_ERROR, TEXT("GPU color/depth readback failed, was not queued, or returned an invalid stride."));
        Session->Pending.Reset();
    }
    if (Session->Preparing) {
        const auto Status = Session->Residency->Status();
        if (Status.phase == HDUE_RESIDENCY_FAILED) {
            // Keep the UI and last preview alive; a newer request can recover.
            Runtime.Error = UTF8_TO_TCHAR(Status.message);
            if (!Session->ResidencyFailed) {
                const auto Residency = Session->Residency;
                ENQUEUE_RENDER_COMMAND(HdueFailedResidency)([Residency](FRHICommandListImmediate&) { Residency->Release(); });
                Session->ResidencyFailed = true;
            }
            return HDUE_OK;
        }
        if (Status.phase == HDUE_RESIDENCY_READY) {
            // Pending preview readback completed, including VT GPU uploads. Start
            // with a new view state so no preview/history can become PT samples.
            FlushRenderingCommands();
            Session->ViewState->Destroy();
            Session->ViewState = Renderer().AllocateViewState(Runtime.World->GetFeatureLevel());
            Session->Preparing = false;
            Session->Draws = 0;
            Session->CameraCut = true;
        }
    }
    {
        FScopeLock Lock(&Session->FrameMutex);
        if (Session->Frame.generation == Session->Request.generation && Session->Frame.converged) return HDUE_OK;
    }
    Draw(Session);
    return HDUE_OK;
}

HdueResult HDUE_CALL PollFrame(HdueSession Handle, HdueFrameInfo* Frame) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Frame || Frame->struct_size != sizeof(*Frame)) return HDUE_INVALID_ARGUMENT;
    const auto Session = Runtime.Session;
    FScopeLock Lock(&Session->FrameMutex);
    if (!Session->HasRequest || !Session->Frame.frame_id || Session->Frame.generation != Session->Request.generation) return HDUE_PENDING;
    *Frame = Session->Frame;
    return HDUE_OK;
}

HdueResult HDUE_CALL CopyFrame(HdueSession Handle, uint64 FrameId, void* Buffer, uint64 Capacity) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Buffer) return HDUE_INVALID_ARGUMENT;
    const auto Session = Runtime.Session;
    FScopeLock Lock(&Session->FrameMutex);
    if (!Session->HasRequest || !FrameId || FrameId != Session->Frame.frame_id || Session->Frame.generation != Session->Request.generation) return HDUE_STALE_FRAME;
    if (Capacity < Session->Frame.required_bytes) return HDUE_BUFFER_TOO_SMALL;
    FMemory::Memcpy(Buffer, Session->Pixels.GetData(), Session->Frame.required_bytes);
    return HDUE_OK;
}

HdueResult HDUE_CALL EnableDepth(HdueSession Handle,uint32 Enabled) {
    if(CheckReady()!=HDUE_OK) return CheckReady();
    if(!ValidSession(Handle) || Enabled>1) return HDUE_INVALID_ARGUMENT;
    const auto Session=Runtime.Session;
    if(Session->DepthEnabled==bool(Enabled)) return HDUE_OK;
    Session->HasRequest=false;
    ReleaseResidency(Session);
    Session->DepthEnabled=bool(Enabled);
    return HDUE_OK;
}
HdueResult HDUE_CALL CopyDepth(HdueSession Handle,uint64 FrameId,float* Buffer,uint64 Capacity) {
    if(CheckReady()!=HDUE_OK) return CheckReady();
    if(!ValidSession(Handle) || !Buffer) return HDUE_INVALID_ARGUMENT;
    const auto Session=Runtime.Session;
    FScopeLock Lock(&Session->FrameMutex);
    if(!Session->DepthEnabled) return HDUE_NOT_SUPPORTED;
    if(!Session->HasRequest || !FrameId || FrameId!=Session->Frame.frame_id || Session->Frame.generation!=Session->Request.generation) return HDUE_STALE_FRAME;
    const uint64 Bytes=uint64(Session->Width)*Session->Height*sizeof(float);
    if(Capacity<Bytes) return HDUE_BUFFER_TOO_SMALL;
    if(Session->DepthPixels.Num()!=int64(Session->Width)*Session->Height) return HDUE_PENDING;
    FMemory::Memcpy(Buffer,Session->DepthPixels.GetData(),Bytes);
    return HDUE_OK;
}

HdueResult HDUE_CALL WindowMessage(uint64 Hwnd, uint32 Message, uint64 Wparam, int64 Lparam, int64* Result) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!Result) return HDUE_INVALID_ARGUMENT;
    *Result = UELibrary_WndProc(reinterpret_cast<HWND>(Hwnd), Message, WPARAM(Wparam), LPARAM(Lparam));
    return HDUE_OK;
}
HdueResult HDUE_CALL ApplyScene(HdueSession Handle, const HdueSceneBatch* Batch) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Batch || Batch->struct_size != sizeof(*Batch)) return HDUE_INVALID_ARGUMENT;
    const auto Session = Runtime.Session;
    Session->HasRequest = false;
    ReleaseResidency(Session);
    FlushRenderingCommands();
    if (!Session->Scene->Apply(*Batch, Runtime.Error)) return HDUE_ENGINE_ERROR;
    Runtime.Error.Empty();
    return HDUE_OK;
}
HdueResult HDUE_CALL Shutdown() {
    if (CheckOwner() != HDUE_OK) return HDUE_WRONG_THREAD;
    if (Runtime.State == HDUE_STATE_STOPPED || Runtime.State == HDUE_STATE_COLD) return HDUE_INVALID_STATE;
    if (Runtime.Session) DestroySession(Runtime.Session->Handle);
    Runtime.World = nullptr;
    if (Runtime.EngineStarted) UELibrary_Shutdown();
    Runtime.EngineStarted = false;
    Runtime.State = HDUE_STATE_STOPPED;
    return HDUE_OK;
}
HdueResult HDUE_CALL PickNative(HdueSession Handle, const HdueNativeRay* Ray, HdueNativeHit* Hit, char* Id, uint32 Capacity) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Ray || !Hit || Ray->struct_size != sizeof(*Ray) || Hit->struct_size != sizeof(*Hit) ||
        !FMath::IsFinite(Ray->minimum_cm) || !FMath::IsFinite(Ray->maximum_cm) || Ray->minimum_cm < 0 || Ray->maximum_cm < Ray->minimum_cm) return HDUE_INVALID_ARGUMENT;
    for (int32 I = 0; I < 3; ++I) if (!FMath::IsFinite(Ray->origin[I]) || !FMath::IsFinite(Ray->direction[I])) return HDUE_INVALID_ARGUMENT;
    if (FVector(Ray->direction[0], Ray->direction[1], Ray->direction[2]).IsNearlyZero()) return HDUE_INVALID_ARGUMENT;
    HdueNativeHit Result{}; Result.struct_size = sizeof(Result);
    FString ObjectId, Error;
    const auto Code = Runtime.Session->Scene->PickNative(*Ray, Result, ObjectId, Error);
    if (Code != HDUE_OK) return Fail(Code, *Error);
    FTCHARToUTF8 Utf8(*ObjectId);
    Result.required_id_bytes = Result.hit ? uint32(Utf8.Length() + 1) : 0;
    *Hit = Result;
    if (Result.hit) {
        if (!Id || Capacity < Result.required_id_bytes) return HDUE_BUFFER_TOO_SMALL;
        FMemory::Memcpy(Id, Utf8.Get(), Result.required_id_bytes);
    }
    return HDUE_OK;
}
HdueResult HDUE_CALL GetPickMaterialInfo(const char* Path, HduePickMaterialInfo* Info) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!Info || Info->struct_size != sizeof(*Info)) return HDUE_INVALID_ARGUMENT;
    FString Error;
    const auto Code = HdueGetPickMaterialInfo(Path, *Info, Error);
    return Code == HDUE_OK ? HDUE_OK : Fail(Code, *Error);
}
HdueResult HDUE_CALL GetRendererInfo(HdueRendererInfo* Info) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!Info || Info->struct_size != sizeof(*Info)) return HDUE_INVALID_ARGUMENT;
    const auto Value = [](const TCHAR* Name) { const IConsoleVariable* V = IConsoleManager::Get().FindConsoleVariable(Name); return V ? V->GetInt() : 0; };
    Info->substrate_enabled = Substrate::IsSubstrateEnabled();
    Info->adaptive_gbuffer = Info->substrate_enabled && !Substrate::IsSubstrateBlendableGBufferEnabled(EShaderPlatform(GMaxRHIShaderPlatform));
    Info->project_closures_per_pixel = Value(TEXT("r.Substrate.ProjectClosuresPerPixel"));
    Info->bytes_per_pixel = Value(TEXT("r.Substrate.BytesPerPixel"));
    return HDUE_OK;
}
HdueResult HDUE_CALL SetMaterialParameters(HdueSession Handle, const HdueMaterialUpdate* Update) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Update || Update->struct_size != sizeof(*Update)) return HDUE_INVALID_ARGUMENT;
    FString Error;
    const auto Code = Runtime.Session->Scene->SetMaterialParameters(*Update, Error);
    if (Code != HDUE_OK) return Fail(Code, *Error);
    Runtime.Session->HasRequest = false;
    ReleaseResidency(Runtime.Session);
    FlushRenderingCommands();
    Runtime.Error.Empty();
    return HDUE_OK;
}
HdueResult HDUE_CALL GetMaterialStats(HdueSession Handle, const char* Id, HdueMaterialStats* Stats) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Stats || Stats->struct_size != sizeof(*Stats)) return HDUE_INVALID_ARGUMENT;
    FString Error;
    const auto Code = Runtime.Session->Scene->GetMaterialStats(Id, *Stats, Error);
    return Code == HDUE_OK ? HDUE_OK : Fail(Code, *Error);
}
HdueResult HDUE_CALL ConfigureResidency(HdueSession Handle, const HdueResidencyOptions* Options) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Options || Options->struct_size != sizeof(*Options) ||
        !FMath::IsFinite(Options->timeout_seconds) || Options->timeout_seconds <= 0) return HDUE_INVALID_ARGUMENT;
    Runtime.Session->HasRequest = false;
    ReleaseResidency(Runtime.Session);
    Runtime.Session->ResidencyOptions = *Options;
    return HDUE_OK;
}
HdueResult HDUE_CALL GetResidencyStatus(HdueSession Handle, HdueResidencyStatus* Status) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Status || Status->struct_size != sizeof(*Status)) return HDUE_INVALID_ARGUMENT;
    *Status = Runtime.Session->Residency ? Runtime.Session->Residency->Status() : HdueResidencyStatus{};
    Status->struct_size = sizeof(*Status);
    return HDUE_OK;
}
HdueResult HDUE_CALL UpsertAsset(HdueSession Handle, const HdueAssetDesc* Desc) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Desc || Desc->struct_size != sizeof(*Desc)) return HDUE_INVALID_ARGUMENT;
    const auto S=Runtime.Session;
    const auto Code=S->Scene->Assets.Upsert(*Desc,Runtime.Error);
    if (Code != HDUE_OK) return Code;
    S->HasRequest=false; ReleaseResidency(S); Runtime.World->SetShouldTick(true);
    return HDUE_OK;
}
HdueResult HDUE_CALL RemoveAsset(HdueSession Handle, const char* Id) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Id) return HDUE_INVALID_ARGUMENT;
    Runtime.Session->Scene->Assets.Remove(UTF8_TO_TCHAR(Id));
    Runtime.Session->HasRequest=false; ReleaseResidency(Runtime.Session); Runtime.World->SetShouldTick(true);
    return HDUE_OK;
}
HdueResult HDUE_CALL AssetStatus(HdueSession Handle, const char* Id, HdueAssetStatus* Status) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Id || !Status || Status->struct_size != sizeof(*Status)) return HDUE_INVALID_ARGUMENT;
    return Runtime.Session->Scene->Assets.GetStatus(UTF8_TO_TCHAR(Id),*Status);
}
HdueResult HDUE_CALL AssetSceneStatus(HdueSession Handle, HdueAssetSceneStatus* Status) {
    if (CheckReady() != HDUE_OK) return CheckReady();
    if (!ValidSession(Handle) || !Status || Status->struct_size != sizeof(*Status)) return HDUE_INVALID_ARGUMENT;
    Runtime.Session->Scene->Assets.GetSceneStatus(*Status); return HDUE_OK;
}
}

extern "C" HDUE_EXPORT HdueResult HDUE_CALL hdue_get_api(uint32_t Version, uint32_t Capacity, HdueApi* Api) {
    if (!Api || Capacity < sizeof(HdueApi)) return HDUE_BUFFER_TOO_SMALL;
    if (Version != HDUE_ABI_VERSION) return HDUE_ABI_MISMATCH;
    *Api = { sizeof(HdueApi), HDUE_ABI_VERSION, Initialize, GetStatus, Tick, CreateSession,
        DestroySession, RequestRender, PollFrame, CopyFrame, WindowMessage, Shutdown, ApplyScene };
    return HDUE_OK;
}
extern "C" HDUE_EXPORT HdueResult HDUE_CALL hdue_get_native_pick_api(uint32 Version, uint32 Capacity, HdueNativePickApi* Api) {
    if (!Api || Capacity < sizeof(*Api)) return HDUE_BUFFER_TOO_SMALL;
    if (Version != HDUE_NATIVE_PICK_ABI_VERSION) return HDUE_ABI_MISMATCH;
    *Api = {sizeof(*Api), HDUE_NATIVE_PICK_ABI_VERSION, PickNative, GetPickMaterialInfo};
    return HDUE_OK;
}
extern "C" HDUE_EXPORT HdueResult HDUE_CALL hdue_get_material_api(uint32 Version, uint32 Capacity, HdueMaterialApi* Api) {
    if (!Api || Capacity < sizeof(*Api)) return HDUE_BUFFER_TOO_SMALL;
    if (Version != HDUE_MATERIAL_ABI_VERSION) return HDUE_ABI_MISMATCH;
    *Api = {sizeof(*Api), HDUE_MATERIAL_ABI_VERSION, GetRendererInfo, SetMaterialParameters, GetMaterialStats};
    return HDUE_OK;
}
extern "C" HDUE_EXPORT HdueResult HDUE_CALL hdue_get_residency_api(uint32 Version, uint32 Capacity, HdueResidencyApi* Api) {
    if (!Api || Capacity < sizeof(*Api)) return HDUE_BUFFER_TOO_SMALL;
    if (Version != HDUE_RESIDENCY_ABI_VERSION) return HDUE_ABI_MISMATCH;
    *Api = {sizeof(*Api), HDUE_RESIDENCY_ABI_VERSION, ConfigureResidency, GetResidencyStatus};
    return HDUE_OK;
}
extern "C" HDUE_EXPORT HdueResult HDUE_CALL hdue_get_asset_api(uint32 Version, uint32 Capacity, HdueAssetApi* Api) {
    if (!Api || Version != HDUE_ASSET_ABI_VERSION || Capacity < sizeof(HdueAssetApi)) return HDUE_INVALID_ARGUMENT;
    *Api={sizeof(HdueAssetApi),HDUE_ASSET_ABI_VERSION,UpsertAsset,RemoveAsset,AssetStatus,AssetSceneStatus}; return HDUE_OK;
}
extern "C" HDUE_EXPORT HdueResult HDUE_CALL hdue_get_depth_api(uint32 Version,uint32 Capacity,HdueDepthApi* Api) {
    if(!Api || Capacity<sizeof(*Api)) return HDUE_BUFFER_TOO_SMALL;
    if(Version!=HDUE_DEPTH_ABI_VERSION) return HDUE_ABI_MISMATCH;
    *Api={sizeof(*Api),HDUE_DEPTH_ABI_VERSION,EnableDepth,CopyDepth}; return HDUE_OK;
}
#endif

