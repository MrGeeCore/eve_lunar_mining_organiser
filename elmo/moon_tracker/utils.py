def user_can_view_scans(user, moon):
    return (
        user_can_delete_scans(user, moon) or
        user.has_perm('eve_sde.sys_can_view_scans', moon.planet.system) or
        user.has_perm('eve_sde.con_can_view_scans', moon.planet.system.constellation) or
        user.has_perm('eve_sde.reg_can_view_scans', moon.planet.system.constellation.region)
    )


def user_can_add_scans(user, moon):
    return (
        user_can_delete_scans(user, moon) or
        user.has_perm('eve_sde.sys_can_add_scans', moon.planet.system) or
        user.has_perm('eve_sde.con_can_add_scans', moon.planet.system.constellation) or
        user.has_perm('eve_sde.reg_can_add_scans', moon.planet.system.constellation.region)
    )


def user_can_delete_scans(user, moon):
    return (
        user.has_perm('eve_sde.sys_can_delete_scans', moon.planet.system) or
        user.has_perm('eve_sde.con_can_delete_scans', moon.planet.system.constellation) or
        user.has_perm('eve_sde.reg_can_delete_scans', moon.planet.system.constellation.region)
    )
