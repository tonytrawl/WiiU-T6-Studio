#!/usr/bin/env python3
"""Locate the console VehicleDef +24 by comparing genuine console body to PC body
against a computed PC field-offset map (natural 4/2/1 alignment, 32-bit ptrs)."""
import struct, transit_oracle_control as T

# --- nested struct field tables (type, count) ; type sizes below ---
PTR = ('ptr', 1)
def F(n=1): return ('f', n)
def I(n=1): return ('i', n)
def U(n=1): return ('u', n)
def S16(n=1): return ('i16', n)
def U16(n=1): return ('u16', n)
def BOOL(n=1): return ('bool', n)

view_limits = [('horiz', 'f', 8)]
VehicleParameter = [
    ('floats28', 'f', 28), ('m_traction_type', 'i', 1), ('m_name', 'ptr', 1),
    ('bbox', 'f', 3*5),  # bbox_min,max,mass_center,buoy_min,buoy_max
    ('water', 'f', 5), ('boat_motor', 'f', 3), ('boat', 'f', 5),
    ('jump', 'f', 1), ('tire_fric_side_max', 'f', 1),
    ('m_drive_on_walls', 'bool', 1),
    ('lin_ang_drag', 'f', 2),
]
VehicleEngineSound = [('name','ptr',1),('alias','u',1),('params','f',5)]  # 28
VehicleGearData = [('g','f',3)]  # 12
VehicleEngine = [
    ('idle_max_torque_brake','f',4),('loadFade','f',4),
    ('loadScale_smooth_lag_pitch','f',4),
    ('onload','sub_ves',5),('offload','sub_ves',5),
    ('numGears','i',1),('loopLastGear','i',1),('gears','sub_gear',10),
]

SIZES = {'f':4,'i':4,'u':4,'ptr':4,'i16':2,'u16':2,'bool':1}

# Explicit flat field list for VehicleDef (T6_Assets.h), natural 4/2/1 alignment,
# 32-bit pointers. Nested structs (_VP/_DBS/_ENG/_ANT) expanded by build().
VEH = [
 ('name','ptr',1),('type','i16',1),
 ('remoteControl','i',1),('bulletDamage','i',1),('armorPiercingDamage','i',1),
 ('grenadeDamage','i',1),('projectileDamage','i',1),('projectileSplashDamage','i',1),
 ('heavyExplosiveDamage','i',1),('cameraMode','i16',1),
 ('autoRecenterOnAccel','i',1),('thirdPersonDriver','i',1),('thirdPersonUseVehicleRoll','i',1),
 ('thirdPersonCameraPitchVehicleRelative','i',1),('thirdPersonCameraHeightWorldRelative','i',1),
 ('thirdPersonCameraRange','f',1),('thirdPersonCameraMinPitchClamp','f',1),
 ('thirdPersonCameraMaxPitchClamp','f',1),('thirdPersonCameraHeight','f',2),
 ('thirdPersonCameraPitch','f',2),('cameraAlwaysAutoCenter','i',1),
 ('cameraAutoCenterLerpRate','f',1),('cameraAutoCenterMaxLerpRate','f',1),
 ('thirdPersonCameraSpringDistance','f',1),('thirdPersonCameraSpringTime','f',1),
 ('thirdPersonCameraHandbrakeTurnRateInc','f',1),('cameraFOV','f',1),('cameraRollFraction','f',1),
 ('tagPlayerOffset','f',3),('killcamCollision','i',1),('killcamDist','f',1),('killcamZDist','f',1),
 ('killcamMinDist','f',1),('killcamZTargetOffset','f',1),('killcamFOV','f',1),
 ('killcamNearBlur','f',1),('killcamNearBlurStart','f',1),('killcamNearBlurEnd','f',1),
 ('killcamFarBlur','f',1),('killcamFarBlurStart','f',1),('killcamFarBlurEnd','f',1),
 ('isDrivable','i',1),('numberOfSeats','i',1),('numberOfGunners','i',1),
 ('seatSwitchOrder','i',11),('driverControlledGunPos','i',1),('entryPointRadius','f',5),
 ('texScrollScale','f',1),('wheelRotRate','f',1),('extraWheelRotScale','f',1),
 ('wheelChildTakesSteerYaw','i',1),('maxSpeed','f',1),('maxSpeedVertical','f',1),
 ('accel','f',1),('accelVertical','f',1),('rotRate','f',1),('rotAccel','f',1),
 ('maxBodyPitch','f',1),('maxBodyRoll','f',1),('collisionDamage','f',1),('collisionSpeed','f',1),
 ('suspensionTravel','f',1),('heliCollisionScalar','f',1),('viewPitchOffset','f',1),
 ('viewInfluence','f',1),('tiltFromAcceleration','f',2),('tiltFromDeceleration','f',2),
 ('tiltFromVelocity','f',2),('tiltSpeed','f',2),
 ('turretWeapon','ptr',1),('turretViewLimits','f',8),('turretRotRate','f',1),
 ('turretClampPlayerView','i',1),('turretLockTurretToPlayerView','i',1),
 ('gunnerWeapon','ptr',4),('gunnerWeaponIndex','u16',4),('gunnerRotRate','f',1),
 ('gunnerRestAngles','f',8),('passengerViewLimits','f',8*6),
 ('sndNames','ptr',2),('sndIndices','u',2),('sndMaterialNames','ptr',3),
 ('skidSpeedMin','f',1),('skidSpeedMax','f',1),('futzName','ptr',1),('futzBlend','f',1),
 ('animType','i',1),('animSet','ptr',1),('scriptedAnimationEntry','i',1),
 ('mantleAngles','f',4),('extraWheelTags','u16',4),('driverHideTag','u16',1),
 ('attachmentModels','ptr',4),('attachmentTags','u16',4),('deathAttachmentModels','ptr',4),
 ('deathAttachmentTags','u16',4),('tracerOffset','f',2),
 ('model','ptr',1),('viewModel','ptr',1),('deathModel','ptr',1),('enemyModel','ptr',1),
 ('modelSwapDelay','f',1),('exhaustFx','ptr',1),('oneExhaust','i',1),('treadFx','ptr',32),
 ('deathFx','ptr',1),('deathFxTag','u16',1),('deathFxSound','ptr',1),
 ('lightFx','ptr',4),('lightFxTag','u16',4),('friendlyLightFx','ptr',1),
 ('friendlyLightFxTag','u16',1),('enemyLightFx','ptr',1),('enemyLightFxTag','u16',1),
 ('radiusDamageMin','f',1),('radiusDamageMax','f',1),('radiusDamageRadius','f',1),
 ('shootShock','ptr',1),('shootRumble','ptr',1),('deathQuakeScale','f',1),
 ('deathQuakeDuration','f',1),('deathQuakeRadius','f',1),('rumbleType','ptr',1),
 ('rumbleScale','f',1),('rumbleDuration','f',1),('rumbleRadius','f',1),
 ('rumbleBaseTime','f',1),('rumbleAdditionalTime','f',1),
 ('healthDefault','i',1),('healthMin','i',1),('healthMax','i',1),('eTeam','i',1),
 ('boostAccelMultiplier','i',1),('boostDuration','f',1),('boostSpeedIncrease','f',1),
 ('addToCompass','i',1),('addToCompassEnemy','i',1),('compassIcon','ptr',1),
 ('compassIconMaterial','ptr',1),('gasButtonName','ptr',1),('gasButton','i',1),
 ('reverseBrakeButtonName','ptr',1),('reverseBrakeButton','i',1),
 ('handBrakeButtonName','ptr',1),('handBrakeButton','i',1),
 ('attackButtonName','ptr',1),('attackButton','i',1),
 ('attackSecondaryButtonName','ptr',1),('attackSecondaryButton','i',1),
 ('boostButtonName','ptr',1),('boostButton','i',1),
 ('moveUpButtonName','ptr',1),('moveUpButton','i',1),
 ('moveDownButtonName','ptr',1),('moveDownButton','i',1),
 ('switchSeatButtonName','ptr',1),('switchSeatButton','i',1),
 ('steerGraphName','ptr',1),('steerGraph','ptr',1),
 ('accelGraphName','ptr',1),('accelGraph','ptr',1),
 ('isNitrous','i',1),('isFourWheelSteering','i',1),('useCollmap','i',1),
 ('radius','f',1),('minHeight','f',1),('maxHeight','f',1),
 ('max_fric_tilt_angle','f',1),('max_fric_tilt','f',1),
 ('noDirectionalDamage','i',1),('fakeBodyStabilizer','i',1),
 ('vehHelicopterBoundsRadius','f',1),('vehHelicopterDecelerationFwd','f',1),
 ('vehHelicopterDecelerationSide','f',1),('vehHelicopterDecelerationUp','f',1),
 ('vehHelicopterTiltFromControllerAxes','f',1),('vehHelicopterTiltFromFwdAndYaw','f',1),
 ('vehHelicopterTiltFromFwdAndYaw_VelAtMaxTilt','f',1),('vehHelicopterTiltMomentum','f',1),
 ('vehHelicopterQuadRotor','i',1),('vehHelicopterAccelTwardsView','i',1),
 ('maxRotorArmMovementAngle','f',1),('maxRotorArmRotationAngle','f',1),
 ('vehHelicopterMaintainHeight','i',1),('vehHelicopterMaintainMaxHeight','i',1),
 ('vehHelicopterMaintainHeightLimit','f',1),('vehHelicopterMaintainHeightAccel','f',1),
 ('vehHelicopterMaintainHeightMinimum','f',1),('vehHelicopterMaintainHeightMaximum','f',1),
 ('vehHelicopterMaintainCeilingMinimum','f',1),
 ('joltVehicle','i',1),('joltVehicleDriver','i',1),('joltMaxTime','f',1),('joltTime','f',1),
 ('joltWaves','f',1),('joltIntensity','f',1),
 ('nitrousVehParams','_VP',1),
 ('driveBySoundRadius','f',2),('driveBySounds','_DBS',40),
 ('doFootSteps','i',1),('isSentient','i',1),
 ('engine','_ENG',1),('antenna','_ANT',2),
 ('csvInclude','ptr',1),('customFloat0','f',1),('customFloat1','f',1),('customFloat2','f',1),
 ('customBool0','i',1),('customBool1','i',1),('customBool2','i',1),
]

def build(offs=0):
    fl=[]; off=[0]
    def align(a):
        if off[0]%a: off[0]+=a-off[0]%a
    def add(name,ty,cnt):
        sz=SIZES[ty]; align(sz)
        for k in range(cnt):
            fl.append((name if cnt==1 else '%s[%d]'%(name,k), ty, off[0])); off[0]+=sz
    def add_vp(name):
        align(4)
        for (fn,ty,cnt) in VehicleParameter: add(name+'.'+fn,ty,cnt)
        align(4)
    def add_dbs(name):
        align(4); add(name+'.apex','i',1); add(name+'.name','ptr',1); add(name+'.alias','u',1); align(4)
    def add_ves(name):
        align(4); add(name+'.name','ptr',1); add(name+'.alias','u',1); add(name+'.params','f',5); align(4)
    def add_gear(name):
        align(4); add(name+'.g','f',3); align(4)
    def add_eng(name):
        align(4)
        add(name+'.head','f',4); add(name+'.loadFade','f',4); add(name+'.mid','f',4)
        for k in range(5): add_ves(name+'.onload%d'%k)
        for k in range(5): add_ves(name+'.offload%d'%k)
        add(name+'.numGears','i',1); add(name+'.loopLastGear','i',1)
        for k in range(10): add_gear(name+'.gear%d'%k)
        align(4)
    def add_ant(name):
        align(4); add(name+'.s','f',4); align(4)
    for (fn,ty,cnt) in VEH:
        if ty=='_VP': add_vp(fn)
        elif ty=='_DBS':
            for k in range(cnt): add_dbs('%s[%d]'%(fn,k))
        elif ty=='_ENG': add_eng(fn)
        elif ty=='_ANT':
            for k in range(cnt): add_ant('%s[%d]'%(fn,k))
        else: add(fn,ty,cnt)
    align(4)
    return fl, off[0]

if __name__=='__main__':
    fl, size = build()
    print('computed PC struct size =', size)
    CO, PC, spans, pcspans, pairs, inserts = T.walk_both()
    cb=CO[42807121:42809803]; pb=PC[44609492:44612150]
    # compare scalar (f/i/u) fields: pc value (LE) vs console value (BE) same offset
    mism=[]
    for (name,ty,off) in fl:
        if ty in ('ptr','bool'): continue
        sz=SIZES[ty]
        if off+sz>len(pb): break
        if ty in ('f','i','u'):
            pv=pb[off:off+4]; cv=cb[off:off+4]
            if pv!=cv[::-1]:
                mism.append((off,name,ty))
        elif ty in ('i16','u16'):
            pv=pb[off:off+2]; cv=cb[off:off+2]
            if pv!=cv[::-1]:
                mism.append((off,name,ty))
    print('first 8 mismatches (pre-insertion should be ptr/hash only):')
    for m in mism[:8]: print('  ',hex(m[0]),m[1],m[2])
    print('total scalar mismatches:',len(mism))
