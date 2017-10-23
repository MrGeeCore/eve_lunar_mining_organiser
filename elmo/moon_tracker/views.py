from django.shortcuts import render, redirect
from django.shortcuts import get_object_or_404
from django.db.models import Count, Sum, Q
from django.views.generic.list import ListView
from django.forms import inlineformset_factory, NumberInput, Select
from django.conf import settings
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.contrib import messages

from collections import defaultdict
from guardian.shortcuts import get_objects_for_user

from eve_auth.models import EveUser
from eve_sde.models import Region, Constellation, SolarSystem
from moon_tracker.utils import user_can_view_scans, user_can_add_scans, user_can_delete_scans
from moon_tracker.models import ScanResult, ScanResultOre, Moon
from moon_tracker.forms import BatchMoonScanForm, OreSearchForm

import logging


logger = logging.getLogger(__name__)


class MoonContainerListView(ListView):
    template_name = 'moon_tracker/grid_list.html'

    def get_context_data(self, **kwargs):
        context = super(MoonContainerListView, self).get_context_data(**kwargs)
        context['parent'] = self.get_parent()
        context['type'] = self.container_type
        return context

    def get_parent(self):
        return None

    def get_queryset(self):
        entity_scanned_count = (
            self.model.objects.raw(self.sql_query, [settings.MOON_TRACKER_MINIMUM_SCANS])
        )

        entity_scanned_map = {x.id: x.num_scanned for x in entity_scanned_count}

        entities = (
            self.get_entities()
            .filter(**{self.system_accessor + 'security__lt': 0.5})
            .annotate(num_moons=Count(self.system_accessor + 'planets__moons'))
        )

        for r in entities:
            r.num_scanned = entity_scanned_map.get(r.id, 0)
            r.fraction_scanned = float(r.num_scanned) / r.num_moons

        return sorted(list(entities), key=lambda r: (-r.fraction_scanned, r.name))


class RegionListView(MoonContainerListView):
    model = Region
    container_type = 'universe'
    id_accessor = 'planet__system__constellation__region__id'
    system_accessor = 'constellations__systems__'

    sql_query = '''
        SELECT r.id, r.name, COUNT(*) as num_scanned
        FROM (
            SELECT * FROM (
                SELECT moon_id AS id, COUNT(*) AS count
                FROM moon_tracker_scanresult
                GROUP BY moon_id
            ) AS moons
            INNER JOIN eve_sde_moon m
            ON (m.id = moons.id)
            WHERE moons.count >= %s
        ) m
        INNER JOIN "eve_sde_planet" p
        ON (m."planet_id" = p."id")
        INNER JOIN "eve_sde_solarsystem" s
        ON (p."system_id" = s."id")
        INNER JOIN "eve_sde_constellation" c
        ON (s."constellation_id" = c."id")
        INNER JOIN "eve_sde_region" r
        ON (c."region_id" = r."id")
        GROUP BY r.id
    '''

    def get_entities(self):
        return self.model.objects.filter(id__lt=11000000)


class ConstellationListView(MoonContainerListView):
    model = Constellation
    container_type = 'region'
    id_accessor = 'planet__system__constellation__id'
    system_accessor = 'systems__'

    sql_query = '''
        SELECT c.id, c.name, COUNT(*) as num_scanned
        FROM (
            SELECT * FROM (
                SELECT moon_id AS id, COUNT(*) AS count
                FROM moon_tracker_scanresult
                GROUP BY moon_id
            ) AS moons
            INNER JOIN eve_sde_moon m
            ON (m.id = moons.id)
            WHERE moons.count >= %s
        ) m
        INNER JOIN "eve_sde_planet" p
        ON (m."planet_id" = p."id")
        INNER JOIN "eve_sde_solarsystem" s
        ON (p."system_id" = s."id")
        INNER JOIN "eve_sde_constellation" c
        ON (s."constellation_id" = c."id")
        GROUP BY c.id
    '''

    def get_entities(self):
        return self.model.objects.filter(region=self.get_parent())

    def get_parent(self):
        return get_object_or_404(Region, name=self.kwargs['region'])


class SolarSystemListView(MoonContainerListView):
    model = SolarSystem
    container_type = 'constellation'
    id_accessor = 'planet__system__id'
    system_accessor = ''

    sql_query = '''
        SELECT s.id, s.name, COUNT(*) as num_scanned
        FROM (
            SELECT * FROM (
                SELECT moon_id AS id, COUNT(*) AS count
                FROM moon_tracker_scanresult
                GROUP BY moon_id
            ) AS moons
            INNER JOIN eve_sde_moon m
            ON (m.id = moons.id)
            WHERE moons.count >= %s
        ) m
        INNER JOIN "eve_sde_planet" p
        ON (m."planet_id" = p."id")
        INNER JOIN "eve_sde_solarsystem" s
        ON (p."system_id" = s."id")
        GROUP BY s.id
    '''

    def get_entities(self):
        return self.model.objects.filter(constellation=self.get_parent())

    def get_parent(self):
        return get_object_or_404(Constellation, name=self.kwargs['constellation'])


def list_system(request, system):
    system_obj = get_object_or_404(SolarSystem.objects.prefetch_related('planets', 'planets__moons'), name=system)

    valid_moons = set()

    for p in (
        Moon.objects
        .filter(planet__system=system_obj)
        .annotate(scan_count=Count('scans'))
        .filter(scan_count__gte=settings.MOON_TRACKER_MINIMUM_SCANS)
        .values_list('id')
    ):
        valid_moons.add(p[0])

    if request.session.get('use_table_view', False):
        template = 'moon_tracker/system_table.html'
    else:
        template = 'moon_tracker/system_list.html'

    return render(
        request,
        template,
        context={
            'moon_list': Moon.objects.filter(planet__system=system_obj).prefetch_related('moonannotation', 'planet'),
            'valid_moons': valid_moons,
            'parent': system_obj,
            'type': 'system',
            'mineral_list': [
                34, 35, 36, 37, 38, 39, 40, 11399, 16634, 16635, 16633, 16636,
                16640, 16639, 16638, 16637, 16643, 16641, 16644, 16642, 16647,
                16648, 16646, 16649, 16650, 16651, 16652, 16653
            ]
        }
    )


def moon_detail(request, system, planet, moon):
    moon_obj = get_object_or_404(Moon, number=moon, planet__number=planet, planet__system__name=system)
    moon_ann = moon_obj.get_annotation()

    scans = ScanResult.objects.filter(moon=moon_obj)

    if not request.user.is_anonymous():
        try:
            user_scan = scans.get(owner=request.user)
        except ScanResult.DoesNotExist:
            user_scan = None
    else:
        user_scan = None

    return render(
        request,
        'moon_tracker/moon_detail.html',
        context={
            'moon': moon_obj,
            'moon_ann': moon_ann,
            'scans': scans,
            'user_scan': user_scan,
            'can_view': user_can_view_scans(request.user, moon_obj),
            'can_add': user_can_add_scans(request.user, moon_obj),
            'can_delete': user_can_delete_scans(request.user, moon_obj),
        }
    )


def batch_submit(request):
    if request.method == 'POST':
        form = BatchMoonScanForm(request.POST)

        if form.is_valid():
            statuses = defaultdict(int)

            for moon_id, materials in form.cleaned_data['data'].items():
                try:
                    moon = Moon.objects.get(id=moon_id)
                except Moon.DoesNotExist:
                    statuses['error_no_moon'] += 1
                    continue

                if not user_can_add_scans(request.user, moon):
                    statuses['error_no_permissions'] += 1
                    continue

                try:
                    moon.add_scan(request.user, materials)
                except ScanResult.AlreadyExistsError:
                    statuses['error_exists_already'] += 1
                    continue

                statuses['success'] += 1

            for status, count in statuses.items():
                if status == 'error_no_moon':
                    messages.error(request, '%d moon scan(s) could not be submitted as the moon did not exist.' % count)
                elif status == 'error_no_permissions':
                    messages.error(request, '%d moon scan(s) could not be submitted as you lack the required permissions.' % count)
                elif status == 'error_exists_already':
                    messages.error(request, '%d moon scan(s) could not be submitted as you have already scanned the moon.' % count)
                elif status == 'success':
                    messages.success(request, '%d moon scan(s) were successfully submitted.' % count)
            return redirect('/')
    else:
        form = BatchMoonScanForm()

    return render(request, 'moon_tracker/batch_submit.html', {'form': form})


def profile(request, uid=None):
    if uid is not None:
        user = get_object_or_404(EveUser, character_id=uid)
    else:
        user = request.user

    scans = (
        ScanResult.objects
        .prefetch_related(
            'moon', 'moon__planet', 'moon__planet__system'
        )
        .filter(owner=user)
        .order_by('id')
    )

    paginator = Paginator(scans, 20)

    page = request.GET.get('page', 1)

    try:
        results = paginator.page(page)
    except PageNotAnInteger:
        results = paginator.page(1)
    except EmptyPage:
        results = paginator.page(paginator.num_pages)

    return render(
        request,
        'moon_tracker/profile.html',
        context={
            'profile_user': user,
            'results': results
        }
    )


def search(request):
    form = OreSearchForm(request.GET)
    results = None

    if form.is_bound and form.is_valid():
        has_permissions = (
            Q(scan__moon__planet__system__constellation__region__in=get_objects_for_user(request.user, 'eve_sde.reg_can_view_scans')) |
            Q(scan__moon__planet__system__constellation__in=get_objects_for_user(request.user, 'eve_sde.con_can_view_scans')) |
            Q(scan__moon__planet__system__in=get_objects_for_user(request.user, 'eve_sde.sys_can_view_scans'))
        )

        scan_ores = (
            ScanResultOre.objects
            .prefetch_related(
                'scan', 'scan__moon', 'scan__moon__planet', 'scan__moon__planet__system'
            )
            .filter(
                has_permissions,
                quantity__gte=form.cleaned_data['min_quantity'],
                ore__in=form.cleaned_data['ore_type'],
            )
            .order_by('-quantity')
        )

        paginator = Paginator(scan_ores, 20)

        page = request.GET.get('page', 1)

        try:
            results = paginator.page(page)
        except PageNotAnInteger:
            results = paginator.page(1)
        except EmptyPage:
            results = paginator.page(paginator.num_pages)

    return render(
        request,
        'moon_tracker/search.html',
        context={
            'form': form,
            'results': results
        }
    )


def leaderboard(request):
    users = (
        EveUser.objects
        .annotate(count=Count('scans'))
        .order_by('-count')
    )[:20]

    ores = (
        ScanResultOre.objects
        .values('ore', 'ore__name')
        .annotate(q=Sum('quantity'))
        .order_by('-q')
    )

    return render(
        request,
        'moon_tracker/leaderboard.html',
        context={
            'users': users,
            'ores': ores,
        }
    )
