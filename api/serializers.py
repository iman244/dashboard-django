from djoser.serializers import UserSerializer as BaseUserSerializer


# djoser's user serializer plus `is_staff`, so the dashboard can gate
# admin-only navigation and actions. `is_staff` is read-only:
# /auth/users/me/ accepts PUT/PATCH through this same serializer, and a
# writable flag would let any user promote themselves to staff.
class UserSerializer(BaseUserSerializer):
    class Meta(BaseUserSerializer.Meta):
        fields = BaseUserSerializer.Meta.fields + ('is_staff',)
        read_only_fields = BaseUserSerializer.Meta.read_only_fields + (
            'is_staff',
        )
