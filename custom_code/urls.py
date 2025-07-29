from django.urls import path

from custom_code.views import TNSTargets, PaperCreateView, scheduling_view, ReferenceStatusUpdateView, ObservationGroupDetailView, observation_sequence_cancel_view, approve_or_reject_observation_view, AuthorshipInformation, download_photometry_view, get_target_standards_view, SNEx2SpectroscopyTNSSharePassthrough, CustomUserUpdateView, TargetFilteringView

app_name = 'custom_code'

urlpatterns = [
    path('tnstargets/', TNSTargets.as_view(), name='tns-targets'),
    path('create-paper/', PaperCreateView.as_view(), name='create-paper'),
    path('scheduling/', scheduling_view, name='scheduling'),
    path('update-reference-status/', ReferenceStatusUpdateView.as_view(), name='update-reference-status'),
    path('observationgroup/<int:pk>/', ObservationGroupDetailView.as_view(), name='observationgroup-detail'),
    path('observation/cancel/', observation_sequence_cancel_view, name='observation-sequence-cancel'),
    path('observation/approve-or-reject/', approve_or_reject_observation_view, name='approve-or-reject-observation'),
    path('authorshipinformation/', AuthorshipInformation.as_view(), name='authorship'),
    path('download-photometry/<int:targetid>/', download_photometry_view, name='download-photometry'),
    path('get-target-standards/', get_target_standards_view, name='get-target-standards'),
    path('tns-share-spectrum/<int:pk>/<int:datum_pk>', SNEx2SpectroscopyTNSSharePassthrough.as_view(), name='tns-share-spectrum'),
    path('users/<int:pk>/update/', CustomUserUpdateView.as_view(), name='custom-user-update'),
    path('target_filter/', TargetFilteringView.as_view(), name='target_filter'),

]
